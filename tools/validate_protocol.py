from __future__ import annotations

import json

from python_detector.ipc.shm_protocol import assert_protocol_layout, protocol_sizes


def main() -> int:
    assert_protocol_layout()
    print(json.dumps(protocol_sizes(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

