import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kotools.resolver import AssetResolver
from kotools.ntf import texture as ko_texture
from _paths import ko as _ko, assets as _assets  # noqa: E402
res=AssetResolver(str(_ko())); res._ensure_archives_loaded()
blob=res.read("item\zhang.dxt")
print("zhang.dxt:", None if blob is None else f"{len(blob)}B  head={blob[:8].hex()}")
# try decode_ntf
try:
    r=ko_texture.decode_ntf(blob); print("decode_ntf OK:", r[0], r[1], "rgba", r[2].shape)
except Exception as e:
    print("decode_ntf FAIL:", type(e).__name__, e)
# try read_dxt_from_bytes (what armor uses)
try:
    from kotools.ntf.texture import read_dxt_from_bytes
    t=read_dxt_from_bytes(blob)
    print("read_dxt_from_bytes OK:", type(t).__name__, [a for a in dir(t) if not a.startswith('_')][:12])
    for attr in ("width","height","rgba","pixels","data","image"):
        if hasattr(t,attr): print("   .",attr,"=", type(getattr(t,attr)).__name__)
except Exception as e:
    print("read_dxt_from_bytes FAIL:", type(e).__name__, e)
