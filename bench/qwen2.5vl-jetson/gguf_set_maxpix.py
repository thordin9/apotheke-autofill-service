"""Copy a GGUF, adding or overriding <arch>.vision.max_pixels.

Ollama caps the image size by this key and sizes its worst-case vision graph by it, so a smaller
value makes qwen2.5vl fit on a 6.8 GB Jetson Xavier NX.
Usage: gguf_set_maxpix.py <src.gguf> <dst.gguf> <max_pixels>   (needs: pip install gguf numpy)
"""

import sys

from gguf import GGUFReader, GGUFValueType, GGUFWriter

src, dst, maxpix = sys.argv[1], sys.argv[2], int(sys.argv[3])
r = GGUFReader(src)
arch = r.fields["general.architecture"].contents()
key = f"{arch}.vision.max_pixels"
w = GGUFWriter(dst, arch=arch, use_temp_file=False)
for name, f in r.fields.items():
    if name.startswith("GGUF.") or name == "general.architecture" or name == key:
        continue
    t = f.types[0]
    if t == GGUFValueType.ARRAY:
        w.add_key_value(name, f.contents(), t, sub_type=f.types[-1])
    else:
        w.add_key_value(name, f.contents(), t)
w.add_uint32(key, maxpix)
print("existing vision keys:", [n for n in r.fields if "vision" in n][:40])
for t in r.tensors:
    w.add_tensor_info(t.name, t.data.shape, t.data.dtype, t.data.nbytes, raw_dtype=t.tensor_type)
w.write_header_to_file()
w.write_kv_data_to_file()
w.write_ti_data_to_file()
for t in r.tensors:
    w.write_tensor_data(t.data)
w.close()
print("wrote", dst, key, "=", maxpix)
