from androguard.core.bytecodes.apk import APK
from androguard.core.bytecodes.dvm import DalvikVMFormat
import sys

apk = APK("/home/marcosab/.gemini/antigravity/scratch/valencia-transit-map/Metrovalencia_1.18.0_APKPure.apk")
for dex in apk.get_all_dex():
    d = DalvikVMFormat(dex)
    for c in d.get_classes():
        if "fgv" in c.get_name().lower() or "retro" in c.get_name().lower() or "http" in c.get_name().lower():
            for m in c.get_methods():
                for i in m.get_instructions():
                    if hasattr(i, 'get_output'):
                        s = i.get_output()
                        if "User-Agent" in s or "horarios-prevision" in s or "Authorization" in s or "Token" in s or "token" in s:
                            print(f"[{c.get_name()} {m.get_name()}] {s}")
