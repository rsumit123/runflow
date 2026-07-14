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
    interval_config = Column(JSON, nullable=True)  # { reps: 8, distance: 250, result: {...} }
    source = Column(String, default="strava", index=True)  # 'strava' | 'garmin'
    average_heartrate = Column(Float, nullable=True)
    max_heartrate = Column(Float, nullable=True)
    average_cadence = Column(Float, nullable=True)
    hr_zones = Column(JSON, nullable=True)            # [{zone, secs}, ...]
    running_dynamics = Column(JSON, nullable=True)    # {stride_length, gct, vertical_oscillation}

    # Conditions this run was actually run in. Without these, a 14 C February run
    # and a 31 C monsoon run get compared as if they were the same effort — which
    # is the flaw in every pace trend this app (and Garmin, and Strava) draws.
    temp_c = Column(Float, nullable=True)
    dew_point_c = Column(Float, nullable=True)
    heat_index = Column(Float, nullable=True)          # temp+dew in F, the coaching table's index
    heat_penalty_sec = Column(Float, nullable=True)    # s/km the conditions cost
    normalized_pace_sec = Column(Float, nullable=True) # pace as it would have been on a cool day
    weather_checked = Column(Boolean, default=False)   # so we don't refetch known-missing days

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
    average_cadence = Column(Float, nullable=True)

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


class DailyWellness(Base):
    """One row per day of Garmin recovery data.

    Cached so a page load doesn't hit Garmin five times, and kept so readiness
    becomes a trend we can reason about rather than a number that vanishes at
    midnight.
    """
    __tablename__ = "daily_wellness"

    date = Column(String, primary_key=True)             # YYYY-MM-DD
    readiness_score = Column(Integer, nullable=True)
    readiness_level = Column(String, nullable=True)     # Garmin's own label
    sleep_hours = Column(Float, nullable=True)
    sleep_score = Column(Integer, nullable=True)
    body_battery_peak = Column(Integer, nullable=True)
    hrv_last_night = Column(Integer, nullable=True)
    hrv_status = Column(String, nullable=True)          # NONE while Garmin onboards it
    resting_hr = Column(Integer, nullable=True)
    raw = Column(JSON, nullable=True)                   # the assessment we derived
    fetched_at = Column(DateTime, nullable=True)


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


class Plan(Base):
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    goal_type = Column(String, nullable=False)          # "5k" | "sprint_100m"
    goal_distance_m = Column(Float, nullable=False)     # 5000 | 100
    target_time_sec = Column(Integer, nullable=False)   # target finish time (whole sec; 5K)
    sprint_target_sec = Column(Float, nullable=True)    # sub-second target (100m sprint)
    start_date = Column(DateTime, nullable=False)
    goal_date = Column(DateTime, nullable=False)
    weeks = Column(Integer, nullable=False)
    status = Column(String, default="active")           # active | completed | abandoned
    created_at = Column(DateTime, nullable=True)
    fitness_snapshot = Column(JSON, nullable=True)      # fitness model at creation
    narrative = Column(JSON, nullable=True)             # {overview, weekly: [..]} | null
    calibrations = Column(JSON, nullable=True)          # audit log: [{date, changes, insights, ...}]

    workouts = relationship("PlannedWorkout", back_populates="plan",
                            cascade="all, delete-orphan")


class PlannedWorkout(Base):
    __tablename__ = "planned_workouts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer, ForeignKey("plans.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    date = Column(DateTime, nullable=False)
    week_number = Column(Integer, nullable=False)
    # 5K: easy | long | quality | strides | rest
    # sprint: accel | max_velocity | speed_endurance | technique | plyometrics | test | rest
    day_type = Column(String, nullable=False)
    target_distance_m = Column(Float, nullable=True)
    pace_low_sec = Column(Integer, nullable=True)       # sec/km (faster bound)
    pace_high_sec = Column(Integer, nullable=True)      # sec/km (slower bound)
    hr_ceiling = Column(Integer, nullable=True)
    title = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    structure = Column(JSON, nullable=True)             # rep scheme / step list {warmup, cooldown, steps, main_set, ...}
    garmin_workout_id = Column(BigInteger, nullable=True)  # pushed to the watch

    plan = relationship("Plan", back_populates="workouts")
