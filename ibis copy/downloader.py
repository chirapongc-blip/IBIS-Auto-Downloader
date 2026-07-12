from pathlib import Path


DOWNLOAD_DIR = Path("downloads")


def ensure_download_dir():
    """
    确保下载目录存在。
    """
    DOWNLOAD_DIR.mkdir(exist_ok=True)


def get_download_dir():
    """
    返回下载目录。
    """
    ensure_download_dir()
    return DOWNLOAD_DIR
