"""Mission evaluation."""

import itertools
import logging
from auvsi_suas.models.mission_judge_feedback import MissionJudgeFeedback
from auvsi_suas.models.odlc import Odlc
from auvsi_suas.models.odlc import OdlcEvaluator
from auvsi_suas.models.takeoff_or_landing_event import TakeoffOrLandingEvent
from auvsi_suas.models.uas_telemetry import UasTelemetry
from auvsi_suas.proto import interop_admin_api_pb2
from django.contrib.auth.models import User

logger = logging.getLogger(__name__)

# The time between interop telemetry posts that's a prereq for other tasks.
INTEROP_TELEM_THRESHOLD_TIME_SEC = 1.0

# Weight of timeline points for mission time.
MISSION_TIME_WEIGHT = 0.8
# Weight of timeline points for not taking a timeout.
TIMEOUT_WEIGHT = 0.2
# Minimum amount of flight time without losing points.
FLIGHT_TIME_MIN_SEC = 20 * 60
# Maximum amount of flight time allowed.
FLIGHT_TIME_MAX_SEC = 30 * 60
# Maximum amount of post process time allowed.
PROCESS_TIME_MAX_SEC = 10 * 60
# Mission time points lost due for every second over max time.
MISSION_TIME_PENALTY_FROM_SEC = 0.03
# Weighting of flight time relative to post process time.
FLIGHT_TO_PROCESS_TIME_WEIGHT = 5.0

# Ratio of points lost per takeover.
AUTONOMOUS_FLIGHT_TAKEOVER = 0.10
# Ratio of points lost per out of bounds.
BOUND_PENALTY = 0.1
SAFETY_BOUND_PENALTY = 1.0
# Ratio of points lost for TFOA and crash.
TFOA_PENALTY = 0.25
CRASH_PENALTY = 0.35
# Weight of flight points to all autonomous flight.
AUTONOMOUS_FLIGHT_FLIGHT_WEIGHT = 0.4
# Weight of capture points to all autonomous flight.
WAYPOINT_CAPTURE_WEIGHT = 0.1
# Weight of accuracy points to all autonomous flight.
WAYPOINT_ACCURACY_WEIGHT = 0.5

# Air delivery accuracy threshold.
AIR_DELIVERY_THRESHOLD_FT = 150.0

# Scoring weights.
TIMELINE_WEIGHT = 0.1
AUTONOMOUS_WEIGHT = 0.2
OBSTACLE_WEIGHT = 0.2
OBJECT_WEIGHT = 0.2
AIR_DELIVERY_WEIGHT = 0.2
OPERATIONAL_WEIGHT = 0.1


def generate_feedback(mission_config, user, team_eval):
    """Generates mission feedback for the given team and mission.

    Args:
        mission_config: The mission to evaluate the team against.
        user: The team user object for which to evaluate and provide feedback.
        team_eval: The team evaluation to fill.
    """
    feedback = team_eval.feedback

    # Find the user's flights.
    flight_periods = TakeoffOrLandingEvent.flights(mission_config, user)
    for period in flight_periods:
        if period.duration() is None:
            team_eval.warnings.append('Infinite flight period.')
    uas_period_logs = [
        UasTelemetry.dedupe(logs)
        for logs in UasTelemetry.by_time_period(user, flight_periods)
    ]
    uas_logs = list(itertools.chain.from_iterable(uas_period_logs))
    if not uas_logs:
        team_eval.warnings.append('No UAS telemetry logs.')

    # Determine interop telemetry rates.
    telem_max, telem_avg = UasTelemetry.rates(
        user, flight_periods, time_period_logs=uas_period_logs)
    if telem_max:
        feedback.uas_telemetry_time_max_sec = telem_max
    if telem_avg:
        feedback.uas_telemetry_time_avg_sec = telem_avg

    # Determine if the uas hit the waypoints.
    feedback.waypoints.extend(
        UasTelemetry.satisfied_waypoints(
            mission_config.home_pos,
            mission_config.mission_waypoints.order_by('order'), uas_logs))

    # Evaluate the object detections.
    user_odlcs = Odlc.objects.filter(user=user).filter(
        mission=mission_config.pk).all()
    evaluator = OdlcEvaluator(user_odlcs,
                              mission_config.odlcs.all(), flight_periods)
    feedback.odlc.CopyFrom(evaluator.evaluate())

    # Determine collisions with stationary.
    for obst in mission_config.stationary_obstacles.all():
        obst_eval = feedback.stationary_obstacles.add()
        obst_eval.id = obst.pk
        obst_eval.hit = obst.evaluate_collision_with_uas(uas_logs)

    # Add judge feedback.
    try:
        judge_feedback = MissionJudgeFeedback.objects.get(
            mission=mission_config.pk, user=user.pk)
        feedback.judge.CopyFrom(judge_feedback.proto())
    except MissionJudgeFeedback.DoesNotExist:
        team_eval.warnings.append('No MissionJudgeFeedback for team.')


def score_team(team_eval):
    """Generates a score from the given feedback.

    Args:
        team_eval: A auvsi_suas.proto.MissionEvaluation for the team.
    """
    feedback = team_eval.feedback
    score = team_eval.score

    # Can't score without judge feedback.
    if not feedback.HasField('judge'):
        team_eval.warnings.append('Cant score due to no judge feedback.')
        return

    # Determine telemetry prerequisite.
    telem_prereq = False
    if (feedback.HasField('uas_telemetry_time_avg_sec') and
            feedback.uas_telemetry_time_avg_sec > 0):
        telem_prereq = feedback.uas_telemetry_time_avg_sec <= INTEROP_TELEM_THRESHOLD_TIME_SEC

    def points_for_time_sec(flight_sec, process_sec):
        return FLIGHT_TO_PROCESS_TIME_WEIGHT * max(
            0, flight_sec - FLIGHT_TIME_MIN_SEC) + process_sec

    # Score timeline.
    timeline = score.timeline
    time_points = points_for_time_sec(feedback.judge.flight_time_sec,
                                      feedback.judge.post_process_time_sec)
    max_time_points = points_for_time_sec(FLIGHT_TIME_MAX_SEC,
                                          PROCESS_TIME_MAX_SEC)
    timeline.mission_time = max(
        0, (max_time_points - time_points) / max_time_points)
    timeline.mission_penalty = MISSION_TIME_PENALTY_FROM_SEC * (
        max(0, feedback.judge.flight_time_sec - FLIGHT_TIME_MAX_SEC) + max(
            0, feedback.judge.post_process_time_sec - PROCESS_TIME_MAX_SEC))
    timeline.timeout = 0 if feedback.judge.used_timeout else 1
    timeline.score_ratio = (
        (MISSION_TIME_WEIGHT * timeline.mission_time) +
        (TIMEOUT_WEIGHT * timeline.timeout) - timeline.mission_penalty)

    # Score autonomous flight.
    flight = score.autonomous_flight
    flight.flight = 1 if feedback.judge.min_auto_flight_time else 0
    flight.telemetry_prerequisite = telem_prereq
    flight.waypoint_capture = (
        float(feedback.judge.waypoints_captured) / len(feedback.waypoints))
    wpt_scores = [w.score_ratio for w in feedback.waypoints]
    if telem_prereq:
        flight.waypoint_accuracy = sum(wpt_scores) / len(feedback.waypoints)
    else:
        flight.waypoint_accuracy = 0
    flight.safety_pilot_takeover_penalty = AUTONOMOUS_FLIGHT_TAKEOVER * feedback.judge.safety_pilot_takeovers
    flight.out_of_bounds_penalty = (
        feedback.judge.out_of_bounds * BOUND_PENALTY +
        feedback.judge.unsafe_out_of_bounds * SAFETY_BOUND_PENALTY)
    if feedback.judge.things_fell_off_uas:
        flight.things_fell_off_penalty = TFOA_PENALTY
    else:
        flight.things_fell_off_penalty = 0
    if feedback.judge.crashed:
        flight.crashed_penalty = CRASH_PENALTY
    else:
        flight.crashed_penalty = 0
    flight.score_ratio = (
        AUTONOMOUS_FLIGHT_FLIGHT_WEIGHT * flight.flight +
        WAYPOINT_CAPTURE_WEIGHT * flight.waypoint_capture +
        WAYPOINT_ACCURACY_WEIGHT * flight.waypoint_accuracy -
        flight.safety_pilot_takeover_penalty - flight.out_of_bounds_penalty -
        flight.things_fell_off_penalty - flight.crashed_penalty)

    # Score obstacle avoidance.
    avoid = score.obstacle_avoidance
    avoid.telemetry_prerequisite = telem_prereq
    if telem_prereq:
        avoid.score_ratio = pow(
            sum([0.0 if o.hit else 1.0 for o in feedback.stationary_obstacles])
            / len(feedback.stationary_obstacles), 3)
    else:
        avoid.score_ratio = 0

    # Score objects.
    objects = score.object
    object_eval = feedback.odlc
    object_field_mapping = [
        ('classifications_score_ratio', 'characteristics'),
        ('geolocation_score_ratio', 'geolocation'),
        ('actionable_score_ratio', 'actionable'),
        ('autonomous_score_ratio', 'autonomy'),
    ]
    for eval_field, score_field in object_field_mapping:
        if object_eval.odlcs:
            total = sum([getattr(o, eval_field) for o in object_eval.odlcs])
            setattr(objects, score_field,
                    float(total) / len(object_eval.odlcs))
        else:
            setattr(objects, score_field, 0)
    objects.extra_object_penalty = object_eval.extra_object_penalty_ratio
    objects.score_ratio = object_eval.score_ratio

    # Score air delivery.
    air = score.air_delivery
    air.delivery_accuracy = feedback.judge.air_delivery_accuracy_ft
    air.score_ratio = max(0,
                          (AIR_DELIVERY_THRESHOLD_FT - air.delivery_accuracy) /
                          AIR_DELIVERY_THRESHOLD_FT)

    # Score operational excellence.
    score.operational_excellence.score_ratio = (
        feedback.judge.operational_excellence_percent / 100.0)

    # Compute total score.
    if feedback.judge.min_auto_flight_time:
        score.score_ratio = max(
            0, TIMELINE_WEIGHT * score.timeline.score_ratio +
            AUTONOMOUS_WEIGHT * score.autonomous_flight.score_ratio +
            OBSTACLE_WEIGHT * score.obstacle_avoidance.score_ratio +
            OBJECT_WEIGHT * score.object.score_ratio +
            AIR_DELIVERY_WEIGHT * score.air_delivery.score_ratio +
            OPERATIONAL_WEIGHT * score.operational_excellence.score_ratio)
    else:
        team_eval.warnings.append(
            'Insufficient flight time to receive any mission points.')
        score.score_ratio = 0


def evaluate_teams(mission_config, users=None):
    """Evaluates the teams (non admin users) of the competition.

    Args:
        mission_config: The mission to evaluate users against.
        users: Optional list of users to eval. If None will evaluate all.
    Returns:
        A auvsi_suas.proto.MultiUserMissionEvaluation.
    """
    # Start a results map from user to MissionEvaluation.
    mission_eval = interop_admin_api_pb2.MultiUserMissionEvaluation()

    # If not provided, eval all users.
    if users is None:
        users = User.objects.all()

    logger.info('Starting team evaluations.')
    for user in sorted(users, key=lambda u: u.username):
        # Ignore admins.
        if user.is_superuser:
            continue

        # Ignore users with no flights for mission.
        if not TakeoffOrLandingEvent.flights(mission_config, user):
            logger.info('Skipping user with no flights: %s' % user.username)
            continue

        # Start the evaluation data structure.
        logger.info('Evaluation starting for user: %s.' % user.username)
        team_eval = mission_eval.teams.add()
        team_eval.team = user.username
        # Generate feedback.
        generate_feedback(mission_config, user, team_eval)
        # Generate score from feedback.
        score_team(team_eval)

    return mission_eval
