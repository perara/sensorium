#include <linux/fs.h>
#include <linux/kernel.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/spi/spidev.h>
#include <linux/uaccess.h>
#include "sensorium-transport-alias-internal.h"

static struct sensorium_device *
sensorium_transport_alias_to_sim(struct file *file)
{
	struct miscdevice *misc = file->private_data;
	struct sensorium_transport_alias *alias =
		container_of(misc, struct sensorium_transport_alias, miscdev);

	return container_of(alias, struct sensorium_device, transport_alias);
}

static ssize_t sensorium_transport_alias_read(struct file *file, char __user *buf,
					      size_t count, loff_t *ppos)
{
	struct sensorium_device *sim = sensorium_transport_alias_to_sim(file);
	struct sensorium_transport_alias *alias = &sim->transport_alias;
	char info[192];
	int len;

	len = scnprintf(info, sizeof(info),
			"device_name=%s\ntransport=%s\ninstance=%s\nadapter=%s\nmode=%u\nbits_per_word=%u\nmax_speed_hz=%u\n",
			sim->transport_device_name, sim->transport->name,
			sim->instance_name, sim->adapter->name,
			alias->spi_mode, alias->spi_bits_per_word,
			alias->spi_max_speed_hz);

	return simple_read_from_buffer(buf, count, ppos, info, len);
}

static ssize_t sensorium_transport_alias_write(struct file *file,
					       const char __user *buf,
					       size_t count, loff_t *ppos)
{
	struct sensorium_device *sim = sensorium_transport_alias_to_sim(file);
	char *kbuf;
	size_t copy_len;

	if (sim->transport->type != SENSORIUM_TRANSPORT_SPI)
		return -EOPNOTSUPP;

	copy_len = min_t(size_t, count, 4096);
	kbuf = memdup_user_nul(buf, copy_len);
	if (IS_ERR(kbuf))
		return PTR_ERR(kbuf);

	/* Simple loopback SPI model: writes are accepted and echoed by message IO. */
	kfree(kbuf);
	return count;
}

static long sensorium_spi_alias_ioctl(struct file *file, unsigned int cmd,
				      unsigned long arg)
{
	struct sensorium_device *sim = sensorium_transport_alias_to_sim(file);
	struct sensorium_transport_alias *alias = &sim->transport_alias;
	u32 value32;
	u8 value8;
	unsigned int nxfers;
	struct spi_ioc_transfer *xfers;
	unsigned int i;
	ssize_t total = 0;

	if (sim->transport->type != SENSORIUM_TRANSPORT_SPI)
		return -ENOTTY;

	switch (cmd) {
	case SPI_IOC_RD_MODE:
	case SPI_IOC_RD_MODE32:
		value32 = alias->spi_mode;
		return copy_to_user((void __user *)arg, &value32,
				    _IOC_SIZE(cmd)) ? -EFAULT : 0;
	case SPI_IOC_WR_MODE:
	case SPI_IOC_WR_MODE32:
		if (copy_from_user(&value32, (void __user *)arg, _IOC_SIZE(cmd)))
			return -EFAULT;
		alias->spi_mode = value32;
		return 0;
	case SPI_IOC_RD_LSB_FIRST:
		value8 = !!(alias->spi_mode & SPI_LSB_FIRST);
		return copy_to_user((void __user *)arg, &value8, sizeof(value8)) ?
			-EFAULT : 0;
	case SPI_IOC_WR_LSB_FIRST:
		if (copy_from_user(&value8, (void __user *)arg, sizeof(value8)))
			return -EFAULT;
		if (value8)
			alias->spi_mode |= SPI_LSB_FIRST;
		else
			alias->spi_mode &= ~SPI_LSB_FIRST;
		return 0;
	case SPI_IOC_RD_BITS_PER_WORD:
		value8 = alias->spi_bits_per_word;
		return copy_to_user((void __user *)arg, &value8, sizeof(value8)) ?
			-EFAULT : 0;
	case SPI_IOC_WR_BITS_PER_WORD:
		if (copy_from_user(&value8, (void __user *)arg, sizeof(value8)))
			return -EFAULT;
		alias->spi_bits_per_word = value8 ?: 8;
		return 0;
	case SPI_IOC_RD_MAX_SPEED_HZ:
		value32 = alias->spi_max_speed_hz;
		return copy_to_user((void __user *)arg, &value32, sizeof(value32)) ?
			-EFAULT : 0;
	case SPI_IOC_WR_MAX_SPEED_HZ:
		if (copy_from_user(&value32, (void __user *)arg, sizeof(value32)))
			return -EFAULT;
		alias->spi_max_speed_hz = value32 ?: 500000;
		return 0;
	default:
		break;
	}

	if (_IOC_TYPE(cmd) != SPI_IOC_MAGIC || _IOC_NR(cmd) != _IOC_NR(SPI_IOC_MESSAGE(0)))
		return -ENOTTY;

	if (_IOC_DIR(cmd) != _IOC_WRITE)
		return -ENOTTY;

	nxfers = _IOC_SIZE(cmd) / sizeof(*xfers);
	if (!nxfers)
		return 0;

	xfers = memdup_user((void __user *)arg, _IOC_SIZE(cmd));
	if (IS_ERR(xfers))
		return PTR_ERR(xfers);

	for (i = 0; i < nxfers; ++i) {
		u8 *tx = NULL;
		u8 *rx = NULL;

		if (!xfers[i].len)
			continue;

		if (xfers[i].tx_buf) {
			tx = memdup_user(u64_to_user_ptr(xfers[i].tx_buf),
					 xfers[i].len);
			if (IS_ERR(tx)) {
				total = PTR_ERR(tx);
				goto out_free;
			}
		}

		if (xfers[i].rx_buf) {
			rx = kzalloc(xfers[i].len, GFP_KERNEL);
			if (!rx) {
				total = -ENOMEM;
				goto out_free;
			}
			if (tx)
				memcpy(rx, tx, xfers[i].len);
			if (copy_to_user(u64_to_user_ptr(xfers[i].rx_buf), rx,
					 xfers[i].len)) {
				total = -EFAULT;
				goto out_free;
			}
		}

		total += xfers[i].len;
out_free:
		kfree(rx);
		kfree(tx);
		if (total < 0)
			break;
	}

	kfree(xfers);
	return total;
}

static const struct file_operations sensorium_transport_alias_fops = {
	.owner = THIS_MODULE,
	.read = sensorium_transport_alias_read,
	.write = sensorium_transport_alias_write,
	.unlocked_ioctl = sensorium_spi_alias_ioctl,
#ifdef CONFIG_COMPAT
	.compat_ioctl = sensorium_spi_alias_ioctl,
#endif
	.llseek = noop_llseek,
};

int sensorium_spi_alias_register(struct sensorium_device *sim)
{
	struct sensorium_transport_alias *alias = &sim->transport_alias;
	int ret;

	strscpy(alias->name, sim->transport_device_name, sizeof(alias->name));
	alias->miscdev.minor = MISC_DYNAMIC_MINOR;
	alias->miscdev.name = alias->name;
	alias->miscdev.fops = &sensorium_transport_alias_fops;
	alias->miscdev.parent = &sim->pdev->dev;
	alias->miscdev.mode = 0660;
	alias->spi_mode = SPI_MODE_0;
	alias->spi_bits_per_word = 8;
	alias->spi_max_speed_hz = 500000;

	ret = misc_register(&alias->miscdev);
	if (ret) {
		pr_err("%s: failed to register SPI transport alias /dev/%s: %d\n",
		       SENSORIUM_DRIVER_NAME, sim->transport_device_name, ret);
		return ret;
	}

	alias->registered = true;
	return 0;
}

void sensorium_spi_alias_unregister(struct sensorium_device *sim)
{
	struct sensorium_transport_alias *alias = &sim->transport_alias;

	if (!alias->registered)
		return;

	misc_deregister(&alias->miscdev);
	alias->registered = false;
}
