#!/usr/bin/env python3
import os
import sys
import logging
import argparse


def _detach_stdin_from_std_handle() -> None:
    """Move the transport's stdin pipe off fd 0 and STD_INPUT_HANDLE (Windows only).

    The stdio transport keeps a read pending on stdin in a worker thread, and Windows
    serialises synchronous I/O on one pipe: any other call that touches it waits until
    the next message arrives. scipy's bundled OpenBLAS DLL, loaded by the first
    `import scipy.linalg` (pyloudnorm and librosa pull it in), probes the standard
    handles as it loads. analyze_loudness then sat for 10 minutes before rendering and
    went on only when the client's cancel arrived on stdin; librosa's first stft and
    onset_detect waited the same way. With fd 0 and STD_INPUT_HANDLE pointing at NUL and
    the transport reading a duplicate of the pipe, those imports took 0.9 and 1.8 s.
    """
    if os.name != "nt" or sys.stdin is None:
        return
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        pipe_fd = os.dup(0)
        nul = os.open(os.devnull, os.O_RDONLY)
        os.dup2(nul, 0)
        os.close(nul)
        set_std_handle = ctypes.windll.kernel32.SetStdHandle
        set_std_handle.argtypes = (wintypes.DWORD, wintypes.HANDLE)
        set_std_handle.restype = wintypes.BOOL
        STD_INPUT_HANDLE = wintypes.DWORD(-10 & 0xFFFFFFFF)
        set_std_handle(STD_INPUT_HANDLE, msvcrt.get_osfhandle(0))
        # The transport wraps sys.stdin.buffer when it starts, so it reads the pipe.
        sys.stdin = open(pipe_fd, "r", encoding="utf-8", errors="replace")
    except Exception as e:
        logging.getLogger("reaper_mcp").warning("stdin left on the standard handle: %s", e)


def main():
    parser = argparse.ArgumentParser(description="REAPER MCP Server")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    from reaper_mcp.server import mcp
    _detach_stdin_from_std_handle()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
