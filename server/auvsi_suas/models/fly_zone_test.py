"""Tests for the fly_zone module."""

import datetime
from auvsi_suas.models import AerialPosition
from auvsi_suas.models import FlyZone
from auvsi_suas.models import GpsPosition
from auvsi_suas.models import UasTelemetry
from auvsi_suas.models import Waypoint
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

TESTDATA_FLYZONE_CONTAINSPOS = [
    # Check can't be inside polygon defined by 1 point
    {
        'min_alt': 0,
        'max_alt': 100,
        'waypoints': [
            (0, 0)
        ],
        'inside_pos': [],
        'outside_pos': [
            (0, 0, 0),
            (0, 0, 100),
            (100, 100, 0),
        ]
    },
    # Check can't be inside polygon defined by 2 points
    {
        'min_alt': 0,
        'max_alt': 100,
        'waypoints': [
            (0, 0),
            (100, 0),
        ],
        'inside_pos': [],
        'outside_pos': [
            (0, 0, 0),
            (0, 0, 100),
            (100, 0, 0),
            (100, 0, 100),
        ]
    },
    # Check polygon of 4 points
    {
        'min_alt': 0,
        'max_alt': 100,
        'waypoints': [
            (0, 0),
            (100, 0),
            (100, 100),
            (0, 100),
        ],
        'inside_pos': [
            (0.1, 0.1, 0),
            (0.1, 0.1, 100),
            (99.9, 0.1, 0),
            (99.9, 0.1, 100),
            (99.9, 99.9, 0),
            (99.9, 99.9, 100),
            (0.1, 99.9, 0),
            (0.1, 99.9, 100),
            (50, 50, 0),
            (50, 50, 50),
            (50, 50, 100),
        ],
        'outside_pos': [
            (0, 0, -1),
            (0, 0, 101),
            (50, 50, -1),
            (50, 50, 101),
            (100, 100, -1),
            (100, 100, 101),
            (-1, 0, 50),
            (0, -1, 50),
            (-1, -1, 50),
            (101, 0, 50),
            (0, 101, 50),
            (101, 101, 50),
        ]
    },
    # Check polygon of 3 points
    {
        'min_alt': 100,
        'max_alt': 750,
        'waypoints': [
            (0, 0),
            (100, 0),
            (50, 100),
        ],
        'inside_pos': [
            (0.1, 0.1, 100),
            (0.1, 0.1, 750),
            (99.9, 0.1, 100),
            (99.9, 0.1, 750),
            (50, 99.9, 100),
            (50, 99.9, 750),
            (25, 25, 100),
            (25, 25, 750),
            (1, 0.1, 100),
            (1, 0.1, 750),
            (99, 0.1, 100),
            (99, 0.1, 750),
            (50, 99, 100),
            (50, 99, 750),
        ],
        'outside_pos': [
            (25, 25, 99),
            (25, 25, 751),
            (-1, 0, 200),
            (0, -1, 200),
            (101, 0, 200),
            (51, 100, 200),
            (50, 101, 200),
        ]
    }
]

# ((alt_min, alt_max, [fly_zone_waypoints]),
#  [(uas_out_bounds_time, [uas_logs])])
TESTDATA_FLYZONE_EVALBOUNDS = (
    [(0, 100, [(38, -76), (39, -76), (39, -77), (38, -77)]),
     (100, 700, [(38, -76), (39, -76), (39, -77), (38, -77)])
     ],
    [(0.0,
      [(38.5, -76.5, 50, 0), (38.5, -76.5, 50, 1.0)]),
     (1.0,
      [(38.5, -76.5, 50, 0), (40, -76.5, 50, 1.0)]),
     (2.0,
      [(38.5, -76.5, 50, 0), (40, -76.5, 50, 1.0), (41, -76.5, 50, 2.0)]),
     (3.0,
      [(38.5, -76.5, 50, 0),
       (40, -76, 50, 1.0),
       (38.5, -76.5, 100, 2.0),
       (38.5, -76.5, 800, 3.0),
       (38.5, -76.5, 600, 4.0),
       (38.5, -78, 100, 5.0)])
     ]
)  # yapf: disable


class TestFlyZone(TestCase):
    """Tests the FlyZone class."""

    def setUp(self):
        """Creates test data."""
        # Form test set for contains position
        self.testdata_containspos = list()
        for test_data in TESTDATA_FLYZONE_CONTAINSPOS:
            # Create the FlyZone
            zone = FlyZone()
            zone.altitude_msl_min = test_data['min_alt']
            zone.altitude_msl_max = test_data['max_alt']
            zone.save()
            for waypoint_id in range(len(test_data['waypoints'])):
                (lat, lon) = test_data['waypoints'][waypoint_id]
                gpos = GpsPosition()
                gpos.latitude = lat
                gpos.longitude = lon
                gpos.save()
                apos = AerialPosition()
                apos.gps_position = gpos
                apos.altitude_msl = 0
                apos.save()
                wpt = Waypoint()
                wpt.order = waypoint_id
                wpt.position = apos
                wpt.save()
                zone.boundary_pts.add(wpt)
            # Form test set
            test_pos = list()
            for pos in test_data['inside_pos']:
                test_pos.append((pos, True))
            for pos in test_data['outside_pos']:
                test_pos.append((pos, False))
            # Store
            self.testdata_containspos.append((zone, test_pos))

    def test_unicode(self):
        """Tests the unicode method executes."""
        zone = FlyZone()
        zone.altitude_msl_min = 1
        zone.altitude_msl_max = 2
        zone.save()
        for _ in range(3):
            pos = GpsPosition()
            pos.latitude = 10
            pos.longitude = 100
            pos.save()
            apos = AerialPosition()
            apos.altitude_msl = 1000
            apos.gps_position = pos
            apos.save()
            wpt = Waypoint()
            wpt.position = apos
            wpt.order = 10
            wpt.save()
            zone.boundary_pts.add(wpt)
        self.assertTrue(zone.__unicode__())

    def test_containsPos(self):
        """Tests the containsPos method."""
        for (zone, test_pos) in self.testdata_containspos:
            for ((lat, lon, alt), inside) in test_pos:
                gpos = GpsPosition()
                gpos.latitude = lat
                gpos.longitude = lon
                gpos.save()
                apos = AerialPosition()
                apos.altitude_msl = alt
                apos.gps_position = gpos
                self.assertEqual(zone.containsPos(apos), inside)

    def test_containsManyPos(self):
        """Tests the containsManyPos method."""
        for (zone, test_pos) in self.testdata_containspos:
            aerial_pos_list = list()
            expected_results = list()
            for ((lat, lon, alt), inside) in test_pos:
                gpos = GpsPosition()
                gpos.latitude = lat
                gpos.longitude = lon
                gpos.save()
                apos = AerialPosition()
                apos.altitude_msl = alt
                apos.gps_position = gpos
                aerial_pos_list.append(apos)
                expected_results.append(inside)
            self.assertEqual(
                zone.containsManyPos(aerial_pos_list), expected_results)

    def test_evaluateUasOutOfBounds(self):
        """Tests the UAS out of bounds method."""
        (zone_details, uas_details) = TESTDATA_FLYZONE_EVALBOUNDS
        # Create FlyZone objects
        zones = list()
        for (alt_min, alt_max, wpts) in zone_details:
            zone = FlyZone()
            zone.altitude_msl_min = alt_min
            zone.altitude_msl_max = alt_max
            zone.save()
            for wpt_id in xrange(len(wpts)):
                (lat, lon) = wpts[wpt_id]
                gpos = GpsPosition()
                gpos.latitude = lat
                gpos.longitude = lon
                gpos.save()
                apos = AerialPosition()
                apos.gps_position = gpos
                apos.altitude_msl = 0
                apos.save()
                wpt = Waypoint()
                wpt.order = wpt_id
                wpt.position = apos
                wpt.save()
                zone.boundary_pts.add(wpt)
            zone.save()
            zones.append(zone)

        # For each user, validate time out of bounds
        user_id = 0
        epoch = timezone.now().replace(
            year=1970,
            month=1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0)
        for exp_out_of_bounds_time, uas_log_details in uas_details:
            # Create the logs
            user = User.objects.create_user('testuser%d' % user_id,
                                            'testemail@x.com', 'testpass')
            user_id += 1
            uas_logs = list()
            for (lat, lon, alt, timestamp) in uas_log_details:
                gpos = GpsPosition()
                gpos.latitude = lat
                gpos.longitude = lon
                gpos.save()
                apos = AerialPosition()
                apos.gps_position = gpos
                apos.altitude_msl = alt
                apos.save()
                log = UasTelemetry()
                log.user = user
                log.uas_position = apos
                log.uas_heading = 0
                log.save()
                log.timestamp = epoch + datetime.timedelta(seconds=timestamp)
                log.save()
                uas_logs.append(log)
            # Assert out of bounds time matches expected
            out_of_bounds_time = FlyZone.evaluateUasOutOfBounds(
                zones, uas_logs)
            self.assertAlmostEqual(out_of_bounds_time, exp_out_of_bounds_time)