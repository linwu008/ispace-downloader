import argparse
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="iSpace 学习资料下载工具")
    parser.add_argument("--data-dir", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    sub.add_parser("sync")
    schedule = sub.add_parser("schedule")
    schedule.add_argument("--time", default="20:00")
    schedule.add_argument("--disable", action="store_true")
    args = parser.parse_args()
    if args.data_dir:
        os.environ["ISPACE_DATA_DIR"] = str(args.data_dir.resolve())
    from .state import Store, data_dir
    if args.command == "serve":
        os.environ["ISPACE_COMPANION_AUTOSTART"] = "1"
        import uvicorn
        uvicorn.run("ispace.web:create_app", factory=True, host="127.0.0.1", port=args.port, access_log=False, **({"log_config": None} if getattr(sys, "frozen", False) else {}))
    elif args.command == "sync":
        from .service import Service, BusyError
        try:
            result = Service(Store(data_dir())).execute("sync")
            print(result.get("message", result["status"]))
            raise SystemExit(0 if result["status"] == "success" else 1)
        except BusyError:
            print("已有同步任务正在运行，本次跳过")
    else:
        from .scheduler import configure
        configure(Store(data_dir()), not args.disable, args.time)
        print("每日检查设置已更新")


if __name__ == "__main__":
    main()
