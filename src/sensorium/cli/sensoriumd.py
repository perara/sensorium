#!/usr/bin/env python3
import argparse
import os
import queue
import sys
import threading
import time
from pathlib import Path

from sensorium.runtime.common import (
    REPO_ROOT,
    RUNTIME_BRIDGE_PATH,
    RUNTIME_BRIDGE_FRAME_LIMIT,
    RUNTIME_SNAPSHOT_PATH,
    RUNTIME_MAX_I2C_MSGS,
    RUNTIME_MAX_PAYLOAD,
    RUNTIME_MAX_SPI_XFERS,
    RUNTIME_SYSTEM_DAEMON_LOG_PATH,
    RUNTIME_SYSTEM_SNAPSHOT_PATH,
    RUNTIME_SYSTEM_STATE_ROOT,
    RUNTIME_SYSTEM_TRACE_PATH,
    RUNTIME_TRACE_PATH,
    runtime_daemon_log_path,
    runtime_pidfile_path,
    runtime_snapshot_path,
    runtime_socket_path,
    runtime_state_root,
    runtime_trace_path,
)
from sensorium.runtime.daemon_support import (
    BUS_CMD_STRUCT,
    DEVICE_CMD_STRUCT,
    UART_MODEM_STRUCT,
    UART_RX_PREFIX_STRUCT,
    I2C_REQ_PREFIX_STRUCT,
    I2C_REQ_MSG_STRUCT,
    SPI_REQ_PREFIX_STRUCT,
    SPI_REQ_XFER_STRUCT,
    UART_REQ_PREFIX_STRUCT,
    UART_CFG_STRUCT,
    REPLY_PREFIX_STRUCT,
    V5_DESC_STRUCT,
    V5_CONTROL_STRUCT,
    V5_SETUP_STRUCT,
    V5_EVENTFDS_STRUCT,
    CMD_RESET,
    CMD_BUS_ADD,
    CMD_BUS_REMOVE,
    CMD_DEVICE_ADD,
    CMD_DEVICE_REMOVE,
    CMD_UART_INJECT_RX,
    CMD_UART_SET_MODEM,
    CMD_REPLY,
    REQ_I2C_XFER,
    REQ_SPI_XFER,
    REQ_UART_TX,
    REQ_UART_CTRL,
    REQ_UART_CFG,
    QUEUE_CLASS_CONTROL,
    QUEUE_CLASS_TRANSPORT,
    QUEUE_CLASS_REPLY,
    FEATURE_SHARED_RINGS,
    FEATURE_EVENTFD_NOTIFY,
    FEATURE_INDEXED_REQUESTS,
    TRANSPORT_IDS,
    UART_MODEM_BITS,
    REQUIRED_FEATURES,
    validate_command_payload,
    encode_c_string,
    decode_c_string,
    parse_tty_name,
    ensure_parent,
    I2CRegisterBankTemplate,
    SPIScriptTemplate,
    UARTScriptTemplate,
    KernelBridge,
)
from sensorium.runtime.backends import RuntimeBackendMixin
from sensorium.runtime.bridge import RuntimeBridgeMixin
from sensorium.runtime.internal import RuntimeInternalMixin
from sensorium.runtime.inventory import RuntimeInventoryMixin
from sensorium.runtime.managed_workers import RuntimeManagedWorkerMixin
from sensorium.runtime.persistence import AsyncTraceWriter
from sensorium.runtime.transport import RuntimeTransportMixin
from sensorium.runtime.rpc import (
    configure_bounded_stdio,
    daemonize_runtime,
    dispatch_runtime_request,
    serve_runtime_daemon,
)
from sensorium.runtime.state import RuntimeStateMixin

KERNEL_TIMEOUT_PARAM_PATH = Path("/sys/module/sensorium/parameters/runtime_timeout_ms")
DEFAULT_KERNEL_TIMEOUT_MS = 1000
DEFAULT_CONTROLLER_TIMEOUT_MARGIN_MS = 100
DEFAULT_BRIDGE_WORKERS = max(2, min(8, (os.cpu_count() or 2)))
DEFAULT_BRIDGE_QUEUE_DEPTH = DEFAULT_BRIDGE_WORKERS * 8
DEFAULT_RPC_WORKERS = DEFAULT_BRIDGE_WORKERS
DEFAULT_RPC_MAX_CLIENTS = 32
DEFAULT_TRACE_LIMIT = 1024
DEFAULT_TRACE_FILE_LIMIT_BYTES = 1024 * 1024
DEFAULT_TRACE_QUEUE_LIMIT = DEFAULT_TRACE_LIMIT
DEFAULT_TRACE_QUEUE_LIMIT_BYTES = 256 * 1024
HEALTH_OK = "ok"
HEALTH_WARN = "warn"
HEALTH_ERROR = "error"
_DEFAULT_PATH = object()


class RuntimeManager(
    RuntimeInternalMixin,
    RuntimeStateMixin,
    RuntimeManagedWorkerMixin,
    RuntimeBackendMixin,
    RuntimeBridgeMixin,
    RuntimeInventoryMixin,
    RuntimeTransportMixin,
):
    HEALTH_OK = HEALTH_OK
    HEALTH_WARN = HEALTH_WARN
    HEALTH_ERROR = HEALTH_ERROR
    CMD_RESET = CMD_RESET

    def __init__(
        self,
        bridge_path: Path,
        *,
        worker_count: int = DEFAULT_BRIDGE_WORKERS,
        max_pending_requests: int = DEFAULT_BRIDGE_QUEUE_DEPTH,
        rpc_workers: int = DEFAULT_RPC_WORKERS,
        rpc_max_clients: int = DEFAULT_RPC_MAX_CLIENTS,
        kernel_timeout_ms: int | None = None,
        controller_timeout_ms: int | None = None,
        trace_limit: int = DEFAULT_TRACE_LIMIT,
        trace_file_limit_bytes: int = DEFAULT_TRACE_FILE_LIMIT_BYTES,
        trace_queue_limit: int = DEFAULT_TRACE_QUEUE_LIMIT,
        trace_queue_limit_bytes: int = DEFAULT_TRACE_QUEUE_LIMIT_BYTES,
        trace_path: Path | None | object = _DEFAULT_PATH,
        snapshot_path: Path | None | object = _DEFAULT_PATH,
        restore_snapshot: bool = True,
    ):
        if trace_path is _DEFAULT_PATH:
            trace_path = runtime_trace_path()
        if snapshot_path is _DEFAULT_PATH:
            snapshot_path = runtime_snapshot_path()
        self.bridge = KernelBridge(bridge_path)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.bridge_thread = None
        self.bridge_workers = []
        self.rpc_server = None
        self.bridge_write_lock = threading.Lock()
        self.route_lock = threading.Lock()
        self.route_locks = {}
        self.worker_count = max(1, int(worker_count))
        self.max_pending_requests = max(self.worker_count, int(max_pending_requests))
        self.rpc_workers = max(1, int(rpc_workers))
        self.rpc_max_clients = max(1, int(rpc_max_clients))
        self.kernel_timeout_ms = (
            self._read_kernel_timeout_ms()
            if kernel_timeout_ms is None
            else max(1, int(kernel_timeout_ms))
        )
        if controller_timeout_ms is None:
            self.controller_timeout_ms = max(
                50, self.kernel_timeout_ms - DEFAULT_CONTROLLER_TIMEOUT_MARGIN_MS
            )
        else:
            self.controller_timeout_ms = max(1, int(controller_timeout_ms))
        self.request_queue = queue.Queue(maxsize=self.max_pending_requests)
        self.trace_limit = max(1, int(trace_limit))
        self.trace_file_limit_bytes = max(1024, int(trace_file_limit_bytes))
        self.trace_queue_limit = max(1, int(trace_queue_limit))
        self.trace_queue_limit_bytes = max(1024, int(trace_queue_limit_bytes))
        self.trace_path = Path(trace_path) if trace_path else None
        self.snapshot_path = Path(snapshot_path) if snapshot_path else None
        self.state_root = runtime_state_root()
        self.restore_snapshot = bool(restore_snapshot)
        self.session_started_ts = round(time.time(), 6)
        self.model_name = None
        self.next_bus_handle = 1
        self.next_device_handle = 1024
        self.buses = {}
        self.devices = {}
        self.devices_by_handle = {}
        self.managed_workers = {}
        self.backend_queues = {}
        self.backend_conds = {}
        self.backend_meta = {}
        self.pending_controller = {}
        self.trace = []
        self.stats_totals = self._fresh_stats()
        self.bridge_stats = self._fresh_bridge_stats()
        self.persistence = self._fresh_persistence()
        self._load_trace_history()
        self.trace_writer = AsyncTraceWriter(
            self.trace_path,
            file_limit_bytes=self.trace_file_limit_bytes,
            trace_snapshot=self._trace_snapshot,
            status_callback=self._set_trace_write_status,
            queue_limit_records=self.trace_queue_limit,
            queue_limit_bytes=self.trace_queue_limit_bytes,
        )
        self.runtime_state = "empty"
        self.generation = 0
        self.desync_reason = None
        self.last_apply_error = None

    def _read_kernel_timeout_ms(self):
        try:
            return max(1, int(KERNEL_TIMEOUT_PARAM_PATH.read_text().strip()))
        except (FileNotFoundError, ValueError, OSError):
            return DEFAULT_KERNEL_TIMEOUT_MS

def parse_args():
    parser = argparse.ArgumentParser(description="Sensorium runtime daemon")
    parser.add_argument("--socket-path", type=Path, default=runtime_socket_path())
    parser.add_argument("--pidfile", type=Path, default=runtime_pidfile_path())
    parser.add_argument("--bridge", type=Path, default=RUNTIME_BRIDGE_PATH)
    parser.add_argument("--bridge-workers", type=int, default=DEFAULT_BRIDGE_WORKERS)
    parser.add_argument("--bridge-queue-depth", type=int, default=DEFAULT_BRIDGE_QUEUE_DEPTH)
    parser.add_argument("--rpc-workers", type=int, default=DEFAULT_RPC_WORKERS)
    parser.add_argument("--rpc-max-clients", type=int, default=DEFAULT_RPC_MAX_CLIENTS)
    parser.add_argument("--controller-timeout-ms", type=int)
    parser.add_argument("--trace-limit", type=int, default=DEFAULT_TRACE_LIMIT)
    parser.add_argument("--trace-file-limit-bytes", type=int, default=DEFAULT_TRACE_FILE_LIMIT_BYTES)
    parser.add_argument("--trace-queue-limit", type=int, default=DEFAULT_TRACE_QUEUE_LIMIT)
    parser.add_argument(
        "--trace-queue-limit-bytes", type=int, default=DEFAULT_TRACE_QUEUE_LIMIT_BYTES
    )
    parser.add_argument("--trace-path", type=Path, default=runtime_trace_path())
    parser.add_argument("--snapshot-path", type=Path, default=runtime_snapshot_path())
    parser.add_argument("--daemon-log-path", type=Path)
    parser.add_argument("--no-restore-snapshot", action="store_true")
    parser.add_argument("--foreground", action="store_true")
    parser.add_argument("--daemonize", action="store_true")
    return parser.parse_args()


def apply_daemon_runtime_defaults(args):
    if not args.daemonize or args.foreground:
        return args
    default_trace_path = runtime_trace_path()
    default_snapshot_path = runtime_snapshot_path()
    default_daemon_log_path = runtime_daemon_log_path()
    if "SENSORIUM_STATE_DIR" not in os.environ:
        os.environ["SENSORIUM_STATE_DIR"] = str(RUNTIME_SYSTEM_STATE_ROOT)
        if args.trace_path == default_trace_path:
            args.trace_path = RUNTIME_SYSTEM_TRACE_PATH
        if args.snapshot_path == default_snapshot_path:
            args.snapshot_path = RUNTIME_SYSTEM_SNAPSHOT_PATH
        if args.daemon_log_path is None:
            args.daemon_log_path = RUNTIME_SYSTEM_DAEMON_LOG_PATH
    elif args.daemon_log_path is None:
        args.daemon_log_path = default_daemon_log_path
    return args


def main():
    args = apply_daemon_runtime_defaults(parse_args())
    if args.daemonize and not args.foreground:
        daemonize_runtime(REPO_ROOT / "scripts" / "runtime" / "sensoriumd", REPO_ROOT, args)
        return 0
    if args.daemon_log_path is not None:
        configure_bounded_stdio(args.daemon_log_path)

    manager = RuntimeManager(
        args.bridge,
        worker_count=args.bridge_workers,
        max_pending_requests=args.bridge_queue_depth,
        rpc_workers=args.rpc_workers,
        rpc_max_clients=args.rpc_max_clients,
        controller_timeout_ms=args.controller_timeout_ms,
        trace_limit=args.trace_limit,
        trace_file_limit_bytes=args.trace_file_limit_bytes,
        trace_queue_limit=args.trace_queue_limit,
        trace_queue_limit_bytes=args.trace_queue_limit_bytes,
        trace_path=args.trace_path,
        snapshot_path=args.snapshot_path,
        restore_snapshot=not args.no_restore_snapshot,
    )
    serve_runtime_daemon(
        args,
        manager,
        lambda request: dispatch_runtime_request(manager, request),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
