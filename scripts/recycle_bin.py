# -*- coding: utf-8 -*-
"""把文件/目录移入回收站（Windows 原生 SHFileOperationW，FOF_ALLOWUNDO）。"""
import ctypes
from ctypes import wintypes


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


FO_DELETE = 3
FOF_ALLOWUNDO = 0x40        # 进回收站
FOF_NOCONFIRMATION = 0x10   # 不弹确认框
FOF_SILENT = 0x4            # 不显示进度
FOF_NOERRORUI = 0x400       # 不弹错误框


def recycle(path):
    """把单个文件或目录移入回收站，返回 0 表示成功。"""
    # pFrom 需要双 \0 结尾；c_wchar_p 会在字符串后再补一个终止符
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = str(path) + "\0"
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return res


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        code = recycle(p)
        print(("OK  " if code == 0 else "FAIL(%d) " % code) + p)
