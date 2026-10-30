"""Business state and the single durable operation ledger."""

import time
import uuid

from sqlalchemy import JSON, BigInteger, Boolean, Column, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

Json = JSON().with_variant(JSONB, "postgresql")
Counter = BigInteger().with_variant(Integer, "sqlite")


def now_ms():
    return time.time_ns() // 1_000_000


def new_id(prefix):
    return prefix + "-" + uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"
    id = Column(String(64), primary_key=True)
    policy = Column(Json, nullable=False, default=dict)
    confirmed = Column(Boolean, nullable=False, default=False)
    created_at = Column(BigInteger, nullable=False, default=now_ms)


class Grant(Base):
    __tablename__ = "grants"
    id = Column(String(64), primary_key=True)
    account_id = Column(ForeignKey("accounts.id"), nullable=False, index=True)
    scope = Column(String(24), nullable=False)
    max_hours = Column(Numeric(12, 6), nullable=False)
    max_cost = Column(Numeric(24, 10))
    client_key = Column(String(160), nullable=False, unique=True)
    created_at = Column(BigInteger, nullable=False, default=now_ms)


class Activity(Base):
    __tablename__ = "activities"
    id = Column(String(200), primary_key=True)
    account_id = Column(ForeignKey("accounts.id"), nullable=False, index=True)
    usage_scope = Column(String(24), nullable=False, default="reproduction")
    owner_version = Column(Integer, nullable=False)
    started_at = Column(BigInteger, nullable=False)
    ended_at = Column(BigInteger)
    lease_until = Column(BigInteger, nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(String(64), primary_key=True)
    account_id = Column(ForeignKey("accounts.id"), nullable=False)
    client_key = Column(String(160), nullable=False, unique=True)
    title = Column(String(200), nullable=False, default="新对话")
    input_version = Column(Integer, nullable=False, default=0)
    model_bindings = Column(Json, nullable=False, default=dict)
    created_at = Column(BigInteger, nullable=False, default=now_ms)


class Project(Base):
    __tablename__ = "projects"
    id = Column(String(64), primary_key=True)
    conversation_id = Column(ForeignKey("conversations.id"), nullable=False, unique=True)
    title = Column(String(200), nullable=False)
    goal = Column(Text, nullable=False)
    source = Column(Text, nullable=False, default="")
    created_at = Column(BigInteger, nullable=True, default=now_ms)


class Message(Base):
    __tablename__ = "messages"
    id = Column(String(180), primary_key=True)
    conversation_id = Column(ForeignKey("conversations.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False, default="")
    status = Column(String(24), nullable=False, default="completed")
    blocks = Column(Json, nullable=False, default=list)
    created_at = Column(BigInteger, nullable=False, default=now_ms)


class Event(Base):
    __tablename__ = "events"
    id = Column(Counter, primary_key=True, autoincrement=True)
    conversation_id = Column(ForeignKey("conversations.id"), nullable=False, index=True)
    kind = Column(String(40), nullable=False)
    payload = Column(Json, nullable=False, default=dict)
    created_at = Column(BigInteger, nullable=False, default=now_ms)


class Source(Base):
    __tablename__ = "sources"
    id = Column(String(100), primary_key=True)
    conversation_id = Column(ForeignKey("conversations.id"), nullable=False, index=True)
    content = Column(Json, nullable=False)


class Card(Base):
    __tablename__ = "cards"
    id = Column(String(64), primary_key=True)
    project_id = Column(ForeignKey("projects.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    input_version = Column(Integer, nullable=False, default=0)
    edit_receipt = Column(Json, nullable=False, default=dict)
    content = Column(Json, nullable=False)
    status = Column(String(24), nullable=False, default="ready")
    created_at = Column(BigInteger, nullable=False, default=now_ms)


class Plan(Base):
    __tablename__ = "plans"
    id = Column(String(64), primary_key=True)
    card_id = Column(ForeignKey("cards.id"), nullable=False, unique=True)
    project_id = Column(ForeignKey("projects.id"), nullable=False, index=True)
    content = Column(Json, nullable=False)
    approved_at = Column(BigInteger, nullable=False, default=now_ms)


class Run(Base):
    __tablename__ = "runs"
    id = Column(String(64), primary_key=True)
    plan_id = Column(ForeignKey("plans.id"), nullable=False, unique=True)
    account_id = Column(ForeignKey("accounts.id"), nullable=False)
    project_id = Column(ForeignKey("projects.id"), nullable=False, index=True)
    status = Column(String(24), nullable=False, default="RUNNING")
    authorization_version = Column(Integer, nullable=False, default=1)
    error = Column(Text)
    container_id = Column(String(128))
    disk_bytes = Column(BigInteger, nullable=False, default=0)
    disk_sample_at = Column(BigInteger, nullable=False, default=0)
    created_at = Column(BigInteger, nullable=False, default=now_ms)
    finished_at = Column(BigInteger)


class AgentSession(Base):
    __tablename__ = "agent_sessions"
    id = Column(String(64), primary_key=True)
    scope_key = Column(String(160), nullable=False, unique=True)
    conversation_id = Column(ForeignKey("conversations.id"), nullable=False, index=True)
    account_id = Column(ForeignKey("accounts.id"), nullable=False)
    kind = Column(String(20), nullable=False)
    model_binding = Column(Json, nullable=False, default=dict)
    run_id = Column(ForeignKey("runs.id"))
    status = Column(String(24), nullable=False, default="QUEUED")
    thread_id = Column(String(100))
    turn_id = Column(String(100))
    owner_version = Column(Integer, nullable=False, default=0)
    owner = Column(String(64))
    lease_token = Column(String(100), unique=True)
    lease_until = Column(BigInteger, nullable=False, default=0)
    state = Column(Json, nullable=False, default=dict)
    error = Column(Text)
    desired_state = Column(String(24), nullable=False, default="ACTIVE")
    retry_at = Column(BigInteger, nullable=False, default=0)
    recovery_count = Column(Integer, nullable=False, default=0)
    idle_turns = Column(Integer, nullable=False, default=0)
    progress_version = Column(Integer, nullable=False, default=0)
    activity_epoch = Column(Integer, nullable=False, default=0)
    created_at = Column(BigInteger, nullable=False, default=now_ms)


class Job(Base):
    __tablename__ = "jobs"
    id = Column(String(64), primary_key=True)
    operation_key = Column(String(200), nullable=False, unique=True)
    account_id = Column(ForeignKey("accounts.id"), nullable=False, index=True)
    session_id = Column(ForeignKey("agent_sessions.id"))
    run_id = Column(ForeignKey("runs.id"), index=True)
    scope = Column(String(20), nullable=False, default="execution")
    authorization_version = Column(Integer, nullable=False, default=1)
    kind = Column(String(32), nullable=False)
    arguments = Column(Json, nullable=False)
    status = Column(String(24), nullable=False, default="QUEUED", index=True)
    owner = Column(String(64))
    owner_version = Column(Integer, nullable=False, default=0)
    lease_until = Column(BigInteger, nullable=False, default=0)
    handle = Column(Json, nullable=False, default=dict)
    progress = Column(Json, nullable=False, default=dict)
    result = Column(Json)
    error = Column(Text)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    reserved_bytes = Column(BigInteger, nullable=False, default=0)
    retry_at = Column(BigInteger, nullable=False, default=0)
    retry_count = Column(Integer, nullable=False, default=0)
    created_at = Column(BigInteger, nullable=False, default=now_ms)
    started_at = Column(BigInteger)
    finished_at = Column(BigInteger)


class Artifact(Base):
    __tablename__ = "artifacts"
    id = Column(String(64), primary_key=True)
    project_id = Column(ForeignKey("projects.id"), nullable=False, index=True)
    run_id = Column(ForeignKey("runs.id"), nullable=False, index=True)
    name = Column(Text, nullable=False)
    sha256 = Column(String(64), nullable=False, index=True)
    size = Column(BigInteger, nullable=False)
    media_type = Column(String(120), nullable=False)
    source_path = Column(Text, nullable=False)
    created_at = Column(BigInteger, nullable=False, default=now_ms)


class Report(Base):
    __tablename__ = "reports"
    id = Column(String(64), primary_key=True)
    run_id = Column(ForeignKey("runs.id"), nullable=False, index=True)
    project_id = Column(ForeignKey("projects.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    result = Column(Json, nullable=False)
    artifact_ids = Column(Json, nullable=False)
    publication = Column(Json, nullable=True)
    export_status = Column(String(24), nullable=False, default="WAITING")
    export_job_id = Column(String(64))
    usage = Column(Json, nullable=False, default=list)
    error = Column(Text)
    created_at = Column(BigInteger, nullable=False, default=now_ms)


class ModelRequest(Base):
    __tablename__ = "model_requests"
    id = Column(String(64), primary_key=True)
    session_id = Column(ForeignKey("agent_sessions.id"), nullable=False, index=True)
    account_id = Column(ForeignKey("accounts.id"), nullable=False, index=True)
    conversation_id = Column(ForeignKey("conversations.id"), nullable=False)
    purpose = Column(String(24), nullable=False)
    usage_scope = Column(String(24), nullable=False, default="reproduction")
    pricing = Column(Json, nullable=False, default=dict)
    connection = Column(Json, nullable=False, default=dict)
    status = Column(String(24), nullable=False)
    reserved_cost = Column(Numeric(24, 10), nullable=False)
    actual_cost = Column(Numeric(24, 10))
    usage = Column(Json)
    provider_response_id = Column(String(200))
    created_at = Column(BigInteger, nullable=False, default=now_ms)
    finished_at = Column(BigInteger)
    lease_until = Column(BigInteger, nullable=False, default=0)
    owner_version = Column(Integer, nullable=False, default=0)
    failure_code = Column(String(80))


class ModelConnection(Base):
    __tablename__ = "model_connections"
    id = Column(String(64), primary_key=True)
    name = Column(String(100), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    config = Column(Json, nullable=False)
    secret = Column(Text, nullable=False, default="")
    enabled = Column(Boolean, nullable=False, default=True)
    deleted = Column(Boolean, nullable=False, default=False)


class UserSettings(Base):
    __tablename__ = "user_settings"
    id = Column(String(24), primary_key=True)
    revision = Column(Integer, nullable=False, default=1)
    content = Column(Json, nullable=False)


class ConnectionTest(Base):
    __tablename__ = "connection_tests"
    id = Column(String(100), primary_key=True)
    connection_id = Column(ForeignKey("model_connections.id"), nullable=False)
    status = Column(String(24), nullable=False, default="REQUESTED")
    result = Column(Json, nullable=False, default=dict)
    created_at = Column(BigInteger, nullable=False, default=now_ms)


class RuntimeEvent(Base):
    __tablename__ = "runtime_events"
    id = Column(String(200), primary_key=True)
    session_id = Column(ForeignKey("agent_sessions.id"), index=True)
    run_id = Column(ForeignKey("runs.id"), index=True)
    kind = Column(String(40), nullable=False)
    payload = Column(Json, nullable=False, default=dict)
    created_at = Column(BigInteger, nullable=False, default=now_ms)
    processed_at = Column(BigInteger)


class InputDelivery(Base):
    __tablename__ = "input_deliveries"
    id = Column(String(180), primary_key=True)
    session_id = Column(ForeignKey("agent_sessions.id"), nullable=False, index=True)
    payload = Column(Json, nullable=False)
    status = Column(String(24), nullable=False, default="PENDING")
    turn_id = Column(String(100))
    owner_version = Column(Integer, nullable=False, default=0)
    created_at = Column(BigInteger, nullable=False, default=now_ms)


class UserQuestion(Base):
    __tablename__ = "user_questions"
    id = Column(String(180), primary_key=True)
    session_id = Column(ForeignKey("agent_sessions.id"), nullable=False, index=True)
    conversation_id = Column(ForeignKey("conversations.id"), nullable=False, index=True)
    item_id = Column(String(180), nullable=False)
    turn_id = Column(String(100), nullable=False)
    blocking = Column(Boolean, nullable=False)
    questions = Column(Json, nullable=False)
    answers = Column(Json)
    answer_key = Column(String(180), unique=True)
    created_at = Column(BigInteger, nullable=False, default=now_ms)
    answered_at = Column(BigInteger)
