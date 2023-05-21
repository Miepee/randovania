import construct
from construct import PrefixedArray, VarInt, Struct, CString

BinStr = CString("utf-8")

BinaryInventory = Struct(
    version=construct.Const(1, VarInt),
    game=BinStr,
    elements=PrefixedArray(
        VarInt,
        Struct(
            name=BinStr,
            amount=VarInt,
            capacity=VarInt,
        )
    )
)

BinaryGameSessionAction = Struct(
    location=VarInt,
    pickup=BinStr,
    provider=BinStr,
    provider_row=VarInt,
    receiver=BinStr,
    time=BinStr,
)
BinaryGameSessionActions = PrefixedArray(VarInt, BinaryGameSessionAction)

BinaryGameSessionAudit = Struct(
    user=BinStr,
    message=BinStr,
    time=BinStr,
)
BinaryGameSessionAuditLog = PrefixedArray(VarInt, BinaryGameSessionAudit)
