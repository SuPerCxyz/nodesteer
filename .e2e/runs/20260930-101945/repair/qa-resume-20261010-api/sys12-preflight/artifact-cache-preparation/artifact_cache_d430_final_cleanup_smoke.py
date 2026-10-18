#!/usr/bin/env python3
"""Local-only rejection tests for final cleanup path guards; no remote or real owned-root access."""
import importlib.util
import tempfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
SOURCE=HERE/"artifact_cache_d430_final_cleanup.py"
spec=importlib.util.spec_from_file_location("d430_final_cleanup_smoke",SOURCE)
cleanup=importlib.util.module_from_spec(spec);spec.loader.exec_module(cleanup)

with tempfile.TemporaryDirectory(prefix="d430-cleanup-guard-",dir=HERE) as temp:
    root=Path(temp)
    good=root/"empty"
    good.mkdir(mode=0o700)
    st=good.lstat()
    assert cleanup.validate_empty_directory(good,st.st_dev,st.st_ino,st.st_uid,st.st_gid,0o700)
    cases=[]
    try:
        cleanup.validate_empty_directory(good,st.st_dev,st.st_ino,st.st_uid+1,st.st_gid,0o700)
        raise AssertionError("foreign uid accepted")
    except cleanup.SafeStop: cases.append("foreign_uid")
    try:
        cleanup.validate_empty_directory(good,st.st_dev,st.st_ino+1,st.st_uid,st.st_gid,0o700)
        raise AssertionError("inode mismatch accepted")
    except cleanup.SafeStop: cases.append("inode_mismatch")
    (good/"unexpected").write_text("marker")
    try:
        cleanup.validate_empty_directory(good,st.st_dev,st.st_ino,st.st_uid,st.st_gid,0o700)
        raise AssertionError("nonempty directory accepted")
    except cleanup.SafeStop: cases.append("nonempty")
    (good/"unexpected").unlink(); good.rmdir()
    target=root/"target";target.mkdir()
    link=root/"link";link.symlink_to(target,target_is_directory=True)
    lst=link.lstat()
    try:
        cleanup.validate_empty_directory(link,lst.st_dev,lst.st_ino,lst.st_uid,lst.st_gid,0o777)
        raise AssertionError("symlink accepted")
    except cleanup.SafeStop: cases.append("symlink")
    link.unlink();target.rmdir()
assert cases==["foreign_uid","inode_mismatch","nonempty","symlink"]
print("foreign/nonempty/symlink/inode-mismatch reject=PASS; exact empty dir accept=PASS; remote actions=0")
