"""Read WeChat's already-stored voice text; no audio upload or invented text.

Known Windows4.1 field shape was researched with the isolated probe and a real
sample. Corrupt, duplicated, status-only or unreviewed shapes remain unread.
"""
def protobuf_fields(value):
    if not isinstance(value, (bytes, bytearray, memoryview)) or len(value) > 65536:
        raise ValueError('unsupported or oversized buffer')
    data, index, fields = bytes(value), 0, []
    def varint():
        nonlocal index
        result = 0
        for shift in range(0, 70, 7):
            if index >= len(data):
                raise ValueError('truncated varint')
            byte = data[index]; index += 1
            if shift == 63 and byte > 1:
                raise ValueError('varint overflow')
            result |= (byte & 127) << shift
            if not byte & 128:
                return result
        raise ValueError('varint overflow')
    while index < len(data):
        tag = varint()
        number, wire = tag >> 3, tag & 7
        if not 0 < number <= 0x1fffffff or len(fields) >= 256:
            raise ValueError('invalid field number or too many fields')
        if wire == 0:
            item = varint()
        elif wire in (1, 2, 5):
            size = varint() if wire == 2 else 8 if wire == 1 else 4
            if size > len(data) - index:
                raise ValueError('truncated bytes')
            item = data[index:index + size]; index += size
        else:
            raise ValueError('unsupported wire type')
        fields.append((number, wire, item))
    return fields


def stored_voice_text(code, packed):
    if int(code) & 0xffffffff != 34:
        return None
    try:
        outer = protobuf_fields(packed)
        containers = [value for n, w, value in outer if (n, w) == (5, 2)]
        if len(containers) != 1:
            return None
        nested = protobuf_fields(containers[0])
        # Only the locally observed completed-text shape is admitted; other
        # field1 values aren't assumed to mean successful conversion.
        if [v for n,w,v in nested if (n,w)==(1,0)] != [2]:
            return None
        texts = [v for n,w,v in nested if (n,w)==(2,2)]
        if len(texts) != 1:
            return None
        text = texts[0].decode('utf-8').strip()
        if not text or len(text) > 12000 or any(ord(c)<32 and c not in '\n\r\t' for c in text):
            return None
        if text in {'转换中', '正在转换', '正在转文字', '转文字失败', '转换失败', '未能识别', '无法识别', '识别失败'}:
            return None
        return text
    except (TypeError, ValueError, UnicodeDecodeError):
        return None
