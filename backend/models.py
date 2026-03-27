from sqlalchemy import (
    Column, BigInteger, Integer, String, Float, Boolean, Text, DateTime, JSON, ForeignKey
)
from sqlalchemy.orm import relationship
from database import Base


class Activity(Base):
    __tablename__ = "activities"

    id = Column(BigInteger, primary_key=True, index=True)  # Strava activity ID
    name = Column(String, nullable=True)
    sport_type = Column(String, nullable=True)
    distance = Column(Float, nullable=True)
    moving_time = Column(Integer, nullable=True)
    elapsed_time = Column(Integer, nullable=True)
    start_date = Column(DateTime, nullable=True)
    average_speed = Column(Float, nullable=True)
    max_speed = Column(Float, nullable=True)
    total_elevation_gain = Column(Float, nullable=True)
    elev_high = Column(Float, nullable=True)
    elev_low = Column(Float, nullable=True)
    start_latlng = Column(JSON, nullable=True)
    end_latlng = Column(JSON, nullable=True)
    map_summary_polyline = Column(Text, nullable=True)
    has_detailed_data = Column(Boolean, default=False)
    is_interval = Column(Boolean, default=False)

    splits = relationship("Split", back_populates="activity", cascade="all, delete-orphan")
    streams = relationship("Stream", back_populates="activity", cascade="all, delete-orphan")
    best_efforts = relationship("BestEffort", back_populates="activity", cascade="all, delete-orphan")


class Split(Base):
    __tablename__ = "splits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    activity_id = Column(BigInteger, ForeignKey("activities.id", ondelete="CASCADE"), nullable=False, index=True)
    split_number = Column(Integer, nullable=False)
    distance = Column(Float, nullable=True)
    moving_time = Column(Integer, nullable=True)
    elapsed_time = Column(Integer, nullable=True)
    average_speed = Column(Float, nullable=True)
    pace_zone = Column(Integer, nullable=True)
    elevation_difference = Column(Float, nullable=True)
    average_heartrate = Column(Float, nullable=True)

    activity = relationship("Activity", back_populates="splits")


class Stream(Base):
    __tablename__ = "streams"

    id = Column(Integer, primary_key=True, autoincrement=True)
    activity_id = Column(BigInteger, ForeignKey("activities.id", ondelete="CASCADE"), nullable=False, index=True)
    stream_type = Column(String, nullable=False)  # e.g. 'latlng', 'altitude', 'distance', 'time', 'velocity_smooth'
    data = Column(JSON, nullable=True)

    activity = relationship("Activity", back_populates="streams")


class BestEffort(Base):
    __tablename__ = "best_efforts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    activity_id = Column(BigInteger, ForeignKey("activities.id", ondelete="CASCADE"), nullable=False, index=True)
    distance_target = Column(Integer, nullable=False)  # target distance in meters (200, 400, 500, 1000)
    time_seconds = Column(Float, nullable=False)  # best time for that distance
    pace_sec_per_km = Column(Float, nullable=True)
    start_index = Column(Integer, nullable=True)  # index in the stream where this segment starts
    end_index = Column(Integer, nullable=True)
    is_dedicated = Column(Boolean, default=False)  # True if total run distance < 2x effort distance

    activity = relationship("Activity", back_populates="best_efforts")


class RouteLabel(Base):
    __tablename__ = "route_labels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    route_key = Column(String, unique=True, nullable=False, index=True)  # stable key for the route
    name = Column(String, nullable=False)


class Goal(Base):
    __tablename__ = "goals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    goal_type = Column(String, nullable=False)  # "speed", "consistency", "volume"
    distance_target = Column(Integer, nullable=True)  # meters (for speed goals)
    time_target = Column(Float, nullable=True)  # seconds (for speed goals)
    weekly_runs_target = Column(Integer, nullable=True)  # for consistency goals
    weekly_km_target = Column(Float, nullable=True)  # for volume goals
    mode = Column(String, nullable=True)  # "sprint" or "any" (for speed goals)
    created_at = Column(DateTime, nullable=True)
    active = Column(Boolean, default=True)


class RouteMerge(Base):
    __tablename__ = "route_merges"

    id = Column(Integer, primary_key=True, autoincrement=True)
    from_key = Column(String, nullable=False, index=True)  # route key being merged away
    to_key = Column(String, nullable=False)  # route key to merge into
