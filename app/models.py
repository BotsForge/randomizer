from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import SQLModel, Field, Relationship, Column, String


class EventType(str, Enum):
    direct = "direct"
    reverse = "reverse"
    reverse_soft = "reverse_soft"


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(sa_column=Column(String, unique=True, index=True))
    password_hash: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

    participants: list["Participant"] = Relationship(back_populates="owner")
    events: list["Event"] = Relationship(back_populates="owner")


class Participant(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    default_weight: int = Field(default=1, ge=1)
    image_url: Optional[str] = None
    user_id: int = Field(foreign_key="user.id")

    owner: Optional[User] = Relationship(back_populates="participants")
    event_links: list["EventParticipant"] = Relationship(back_populates="participant")


class PrizeItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    image_url: Optional[str] = None
    event_id: int = Field(foreign_key="event.id")

    event: Optional["Event"] = Relationship(back_populates="prize")


class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    description: str
    slug: str = Field(sa_column=Column(String, unique=True, index=True))
    event_type: EventType = Field(default=EventType.direct)
    starts_at: datetime
    in_progress: bool = False
    finished: bool = False
    user_id: int = Field(foreign_key="user.id")

    owner: Optional[User] = Relationship(back_populates="events")
    prize: Optional[PrizeItem] = Relationship(back_populates="event")
    participants: list["EventParticipant"] = Relationship(back_populates="event")
    spin_results: list["SpinResult"] = Relationship(back_populates="event")

    async def fetch_participants(self, session) -> list[Participant]:
        # returns participants joined to this event
        from sqlmodel import select
        result = await session.exec(
            select(Participant).join(EventParticipant).where(EventParticipant.event_id == self.id)
        )
        return result.all()

    def weight_for_part(self, p: Participant) -> int:
        # find link weight override
        for link in self.participants:
            if link.participant_id == p.id:
                return link.weight or p.default_weight
        return p.default_weight


class EventParticipant(SQLModel, table=True):
    event_id: Optional[int] = Field(default=None, foreign_key="event.id", primary_key=True)
    participant_id: Optional[int] = Field(default=None, foreign_key="participant.id", primary_key=True)
    weight: Optional[int] = Field(default=None, ge=1)

    event: Optional[Event] = Relationship(back_populates="participants")
    participant: Optional[Participant] = Relationship(back_populates="event_links")


class SpinResult(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: int = Field(foreign_key="event.id")
    participant_id: int = Field(foreign_key="participant.id")
    eliminated: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)

    event: Optional[Event] = Relationship(back_populates="spin_results")
