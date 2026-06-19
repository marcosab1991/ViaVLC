from androguard.core.bytecodes.apk import APK
from androguard.core.bytecodes.dvm import DalvikVMFormat

apk = APK("/home/marcosab/.gemini/antigravity/scratch/valencia-transit-map/Metrovalencia_1.18.0_APKPure.apk")
for dex in apk.get_all_dex():
    d = DalvikVMFormat(dex)
    for c in d.get_classes():
        if "fgv" in c.get_name().lower() and "transporte" in c.get_name().lower():
            for m in c.get_methods():
                for i in m.get_instructions():
                    if hasattr(i, 'get_output'):
                        s = i.get_output()
                        if "http" in s or "token" in s.lower() or "auth" in s.lower() or "api" in s.lower() or "user-agent" in s.lower() or "key" in s.lower():
                            print(f"[{c.get_name()} {m.get_name()}] {s}")
