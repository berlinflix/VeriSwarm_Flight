from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Vote(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    VOTE_UNSPECIFIED: _ClassVar[Vote]
    ACK: _ClassVar[Vote]
    DISPUTE: _ClassVar[Vote]
VOTE_UNSPECIFIED: Vote
ACK: Vote
DISPUTE: Vote

class Receipt(_message.Message):
    __slots__ = ("drone_id", "timestamp_ns", "input_hash", "model_hash", "output", "nonce", "protocol_version", "mission_id", "mission_epoch", "sequence", "runtime_hash", "action_frame", "valid_for_ns", "pose_enu", "pose_timestamp_ns", "pose_uncertainty_m")
    DRONE_ID_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_NS_FIELD_NUMBER: _ClassVar[int]
    INPUT_HASH_FIELD_NUMBER: _ClassVar[int]
    MODEL_HASH_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_FIELD_NUMBER: _ClassVar[int]
    NONCE_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    MISSION_ID_FIELD_NUMBER: _ClassVar[int]
    MISSION_EPOCH_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_HASH_FIELD_NUMBER: _ClassVar[int]
    ACTION_FRAME_FIELD_NUMBER: _ClassVar[int]
    VALID_FOR_NS_FIELD_NUMBER: _ClassVar[int]
    POSE_ENU_FIELD_NUMBER: _ClassVar[int]
    POSE_TIMESTAMP_NS_FIELD_NUMBER: _ClassVar[int]
    POSE_UNCERTAINTY_M_FIELD_NUMBER: _ClassVar[int]
    drone_id: str
    timestamp_ns: int
    input_hash: str
    model_hash: str
    output: _containers.RepeatedScalarFieldContainer[float]
    nonce: str
    protocol_version: int
    mission_id: str
    mission_epoch: int
    sequence: int
    runtime_hash: str
    action_frame: str
    valid_for_ns: int
    pose_enu: _containers.RepeatedScalarFieldContainer[float]
    pose_timestamp_ns: int
    pose_uncertainty_m: float
    def __init__(self, drone_id: _Optional[str] = ..., timestamp_ns: _Optional[int] = ..., input_hash: _Optional[str] = ..., model_hash: _Optional[str] = ..., output: _Optional[_Iterable[float]] = ..., nonce: _Optional[str] = ..., protocol_version: _Optional[int] = ..., mission_id: _Optional[str] = ..., mission_epoch: _Optional[int] = ..., sequence: _Optional[int] = ..., runtime_hash: _Optional[str] = ..., action_frame: _Optional[str] = ..., valid_for_ns: _Optional[int] = ..., pose_enu: _Optional[_Iterable[float]] = ..., pose_timestamp_ns: _Optional[int] = ..., pose_uncertainty_m: _Optional[float] = ...) -> None: ...

class SignedReceipt(_message.Message):
    __slots__ = ("receipt", "signature_hex")
    RECEIPT_FIELD_NUMBER: _ClassVar[int]
    SIGNATURE_HEX_FIELD_NUMBER: _ClassVar[int]
    receipt: Receipt
    signature_hex: str
    def __init__(self, receipt: _Optional[_Union[Receipt, _Mapping]] = ..., signature_hex: _Optional[str] = ...) -> None: ...

class PeerVote(_message.Message):
    __slots__ = ("voter_id", "target_receipt_hash", "decision", "reason", "timestamp_ns")
    VOTER_ID_FIELD_NUMBER: _ClassVar[int]
    TARGET_RECEIPT_HASH_FIELD_NUMBER: _ClassVar[int]
    DECISION_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_NS_FIELD_NUMBER: _ClassVar[int]
    voter_id: str
    target_receipt_hash: str
    decision: Vote
    reason: str
    timestamp_ns: int
    def __init__(self, voter_id: _Optional[str] = ..., target_receipt_hash: _Optional[str] = ..., decision: _Optional[_Union[Vote, str]] = ..., reason: _Optional[str] = ..., timestamp_ns: _Optional[int] = ...) -> None: ...

class SignedVote(_message.Message):
    __slots__ = ("vote", "signature_hex")
    VOTE_FIELD_NUMBER: _ClassVar[int]
    SIGNATURE_HEX_FIELD_NUMBER: _ClassVar[int]
    vote: PeerVote
    signature_hex: str
    def __init__(self, vote: _Optional[_Union[PeerVote, _Mapping]] = ..., signature_hex: _Optional[str] = ...) -> None: ...

class PushVoteAck(_message.Message):
    __slots__ = ("accepted", "reason")
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    accepted: bool
    reason: str
    def __init__(self, accepted: bool = ..., reason: _Optional[str] = ...) -> None: ...

class PingRequest(_message.Message):
    __slots__ = ("from_drone_id",)
    FROM_DRONE_ID_FIELD_NUMBER: _ClassVar[int]
    from_drone_id: str
    def __init__(self, from_drone_id: _Optional[str] = ...) -> None: ...

class PingResponse(_message.Message):
    __slots__ = ("this_drone_id", "timestamp_ns")
    THIS_DRONE_ID_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_NS_FIELD_NUMBER: _ClassVar[int]
    this_drone_id: str
    timestamp_ns: int
    def __init__(self, this_drone_id: _Optional[str] = ..., timestamp_ns: _Optional[int] = ...) -> None: ...
