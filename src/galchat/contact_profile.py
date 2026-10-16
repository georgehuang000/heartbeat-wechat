"""Bounded reader for the observed WeChat 4 contact protobuf gender field.

Only field 2 / varint is used. Unknown, conflicting or malformed formats stay
unknown; no names, signatures, regions or telephone fields are extracted.
"""

def contact_gender(buffer):
    if not isinstance(buffer,(bytes,bytearray,memoryview)) or len(buffer)>65536:
        return 0
    data=bytes(buffer)
    index=0
    genders=[]
    def varint():
        nonlocal index
        value=0
        for shift in range(0,70,7):
            if index>=len(data):raise ValueError()
            byte=data[index];index+=1
            if shift==63 and byte>1:raise ValueError()
            value|=(byte&127)<<shift
            if not byte&128:return value
        raise ValueError()
    try:
        while index<len(data):
            tag=varint();field,wire=tag>>3,tag&7
            if field==0:raise ValueError()
            if wire==0:
                value=varint()
                if field==2:genders.append(value)
            elif wire in (1,5):index+=8 if wire==1 else 4
            elif wire==2:
                length=varint();index+=length
            else:raise ValueError()
            if index>len(data):raise ValueError()
    except ValueError:
        return 0
    return genders[0] if genders and len(set(genders))==1 and genders[0] in (1,2) else 0
