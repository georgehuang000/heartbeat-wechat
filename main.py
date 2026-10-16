from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

if __name__ == "__main__":
    from galchat.runtime_paths import prepare_windowed_streams, report_startup_error
    prepare_windowed_streams()
    try:
        from galchat.app import main
        main()
    except Exception:
        if not getattr(sys, 'frozen', False):
            raise
        report_startup_error()
        raise SystemExit(1)
