"""The test for the statistics sensor platform."""
from datetime import datetime, timedelta
import statistics
import unittest
from unittest.mock import patch

import pytest

from homeassistant import config as hass_config
from homeassistant.components import recorder
from homeassistant.components.sensor import ATTR_STATE_CLASS, STATE_CLASS_MEASUREMENT
from homeassistant.components.statistics.sensor import DOMAIN, StatisticsSensor
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    SERVICE_RELOAD,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    TEMP_CELSIUS,
)
from homeassistant.setup import async_setup_component, setup_component
from homeassistant.util import dt as dt_util

from tests.common import (
    fire_time_changed,
    get_fixture_path,
    get_test_home_assistant,
    init_recorder_component,
)
from tests.components.recorder.common import wait_recording_done


@pytest.fixture(autouse=True)
def mock_legacy_time(legacy_patchable_time):
    """Make time patchable for all the tests."""
    yield


class TestStatisticsSensor(unittest.TestCase):
    """Test the Statistics sensor."""

    def setup_method(self, method):
        """Set up things to be run when tests are started."""
        self.hass = get_test_home_assistant()
        self.values = [17, 20, 15.2, 5, 3.8, 9.2, 6.7, 14, 6]
        self.count = len(self.values)
        self.min = min(self.values)
        self.max = max(self.values)
        self.total = sum(self.values)
        self.mean = round(sum(self.values) / len(self.values), 2)
        self.median = round(statistics.median(self.values), 2)
        self.deviation = round(statistics.stdev(self.values), 2)
        self.variance = round(statistics.variance(self.values), 2)
        self.quantiles = [
            round(quantile, 2) for quantile in statistics.quantiles(self.values)
        ]
        self.change = round(self.values[-1] - self.values[0], 2)
        self.average_change = round(self.change / (len(self.values) - 1), 2)
        self.change_rate = round(self.change / (60 * (self.count - 1)), 2)
        self.addCleanup(self.hass.stop)

    def test_binary_sensor_source(self):
        """Test if source is a sensor."""
        values = ["on", "off", "on", "off", "on", "off", "on"]
        assert setup_component(
            self.hass,
            "sensor",
            {
                "sensor": [
                    {
                        "platform": "statistics",
                        "name": "test",
                        "entity_id": "binary_sensor.test_monitored",
                    },
                    {
                        "platform": "statistics",
                        "name": "test_unitless",
                        "entity_id": "binary_sensor.test_monitored_unitless",
                    },
                ]
            },
        )

        self.hass.block_till_done()
        self.hass.start()
        self.hass.block_till_done()

        for value in values:
            self.hass.states.set(
                "binary_sensor.test_monitored",
                value,
                {ATTR_UNIT_OF_MEASUREMENT: TEMP_CELSIUS},
            )
            self.hass.states.set("binary_sensor.test_monitored_unitless", value)
            self.hass.block_till_done()

        state = self.hass.states.get("sensor.test")
        assert state.state == str(len(values))
        assert state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) is None
        assert state.attributes.get(ATTR_STATE_CLASS) == STATE_CLASS_MEASUREMENT

        state = self.hass.states.get("sensor.test_unitless")
        assert state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) is None

    def test_sensor_source(self):
        """Test if source is a sensor."""
        assert setup_component(
            self.hass,
            "sensor",
            {
                "sensor": {
                    "platform": "statistics",
                    "name": "test",
                    "entity_id": "sensor.test_monitored",
                }
            },
        )

        self.hass.block_till_done()
        self.hass.start()
        self.hass.block_till_done()

        for value in self.values:
            self.hass.states.set(
                "sensor.test_monitored",
                value,
                {ATTR_UNIT_OF_MEASUREMENT: TEMP_CELSIUS},
            )
            self.hass.block_till_done()

        state = self.hass.states.get("sensor.test")
        assert str(self.mean) == state.state
        assert self.min == state.attributes.get("min_value")
        assert self.max == state.attributes.get("max_value")
        assert self.variance == state.attributes.get("variance")
        assert self.median == state.attributes.get("median")
        assert self.deviation == state.attributes.get("standard_deviation")
        assert self.quantiles == state.attributes.get("quantiles")
        assert self.mean == state.attributes.get("mean")
        assert self.count == state.attributes.get("count")
        assert self.total == state.attributes.get("total")
        assert self.change == state.attributes.get("change")
        assert self.average_change == state.attributes.get("average_change")

        assert state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) == TEMP_CELSIUS
        assert state.attributes.get(ATTR_STATE_CLASS) == STATE_CLASS_MEASUREMENT

        # Source sensor turns unavailable, then available with valid value,
        # statistics sensor should follow
        state = self.hass.states.get("sensor.test")
        self.hass.states.set(
            "sensor.test_monitored",
            STATE_UNAVAILABLE,
        )
        self.hass.block_till_done()
        new_state = self.hass.states.get("sensor.test")
        assert new_state.state == STATE_UNAVAILABLE
        self.hass.states.set(
            "sensor.test_monitored",
            0,
            {ATTR_UNIT_OF_MEASUREMENT: TEMP_CELSIUS},
        )
        self.hass.block_till_done()
        new_state = self.hass.states.get("sensor.test")
        assert new_state.state != STATE_UNAVAILABLE
        assert new_state.attributes.get("count") == state.attributes.get("count") + 1

        # Source sensor has a non-float state, unit and state should not change
        state = self.hass.states.get("sensor.test")
        self.hass.states.set("sensor.test_monitored", "beer", {})
        self.hass.block_till_done()
        new_state = self.hass.states.get("sensor.test")
        assert state == new_state

        # Source sensor is removed, unit and state should not change
        # This is equal to a None value being published
        self.hass.states.remove("sensor.test_monitored")
        self.hass.block_till_done()
        new_state = self.hass.states.get("sensor.test")
        assert state == new_state

    def test_sampling_size(self):
        """Test rotation."""
        assert setup_component(
            self.hass,
            "sensor",
            {
                "sensor": {
                    "platform": "statistics",
                    "name": "test",
                    "entity_id": "sensor.test_monitored",
                    "sampling_size": 5,
                }
            },
        )

        self.hass.block_till_done()
        self.hass.start()
        self.hass.block_till_done()

        for value in self.values:
            self.hass.states.set(
                "sensor.test_monitored",
                value,
                {ATTR_UNIT_OF_MEASUREMENT: TEMP_CELSIUS},
            )
            self.hass.block_till_done()

        state = self.hass.states.get("sensor.test")

        assert state.attributes.get("min_value") == 3.8
        assert state.attributes.get("max_value") == 14

    def test_sampling_size_1(self):
        """Test validity of stats requiring only one sample."""
        assert setup_component(
            self.hass,
            "sensor",
            {
                "sensor": {
                    "platform": "statistics",
                    "name": "test",
                    "entity_id": "sensor.test_monitored",
                    "sampling_size": 1,
                }
            },
        )

        self.hass.block_till_done()
        self.hass.start()
        self.hass.block_till_done()

        for value in self.values[-3:]:  # just the last 3 will do
            self.hass.states.set(
                "sensor.test_monitored",
                value,
                {ATTR_UNIT_OF_MEASUREMENT: TEMP_CELSIUS},
            )
            self.hass.block_till_done()

        state = self.hass.states.get("sensor.test")

        # require only one data point
        assert self.values[-1] == state.attributes.get("min_value")
        assert self.values[-1] == state.attributes.get("max_value")
        assert self.values[-1] == state.attributes.get("mean")
        assert self.values[-1] == state.attributes.get("median")
        assert self.values[-1] == state.attributes.get("total")
        assert state.attributes.get("change") == 0
        assert state.attributes.get("average_change") == 0

        # require at least two data points
        assert state.attributes.get("variance") == STATE_UNKNOWN
        assert state.attributes.get("standard_deviation") == STATE_UNKNOWN
        assert state.attributes.get("quantiles") == STATE_UNKNOWN

    def test_max_age(self):
        """Test value deprecation."""
        now = dt_util.utcnow()
        mock_data = {
            "return_time": datetime(now.year + 1, 8, 2, 12, 23, tzinfo=dt_util.UTC)
        }

        def mock_now():
            return mock_data["return_time"]

        with patch(
            "homeassistant.components.statistics.sensor.dt_util.utcnow", new=mock_now
        ):
            assert setup_component(
                self.hass,
                "sensor",
                {
                    "sensor": {
                        "platform": "statistics",
                        "name": "test",
                        "entity_id": "sensor.test_monitored",
                        "max_age": {"minutes": 3},
                    }
                },
            )

            self.hass.block_till_done()
            self.hass.start()
            self.hass.block_till_done()

            for value in self.values:
                self.hass.states.set(
                    "sensor.test_monitored",
                    value,
                    {ATTR_UNIT_OF_MEASUREMENT: TEMP_CELSIUS},
                )
                self.hass.block_till_done()
                # insert the next value one minute later
                mock_data["return_time"] += timedelta(minutes=1)

            state = self.hass.states.get("sensor.test")

        assert state.attributes.get("min_value") == 6
        assert state.attributes.get("max_value") == 14

    def test_max_age_without_sensor_change(self):
        """Test value deprecation."""
        now = dt_util.utcnow()
        mock_data = {
            "return_time": datetime(now.year + 1, 8, 2, 12, 23, tzinfo=dt_util.UTC)
        }

        def mock_now():
            return mock_data["return_time"]

        with patch(
            "homeassistant.components.statistics.sensor.dt_util.utcnow", new=mock_now
        ):
            assert setup_component(
                self.hass,
                "sensor",
                {
                    "sensor": {
                        "platform": "statistics",
                        "name": "test",
                        "entity_id": "sensor.test_monitored",
                        "max_age": {"minutes": 3},
                    }
                },
            )

            self.hass.block_till_done()
            self.hass.start()
            self.hass.block_till_done()

            for value in self.values:
                self.hass.states.set(
                    "sensor.test_monitored",
                    value,
                    {ATTR_UNIT_OF_MEASUREMENT: TEMP_CELSIUS},
                )
                self.hass.block_till_done()
                # insert the next value 30 seconds later
                mock_data["return_time"] += timedelta(seconds=30)

            state = self.hass.states.get("sensor.test")

            assert state.attributes.get("min_value") == 3.8
            assert state.attributes.get("max_value") == 15.2

            # wait for 3 minutes (max_age).
            mock_data["return_time"] += timedelta(minutes=3)
            fire_time_changed(self.hass, mock_data["return_time"])
            self.hass.block_till_done()

            state = self.hass.states.get("sensor.test")

            assert state.attributes.get("min_value") == STATE_UNKNOWN
            assert state.attributes.get("max_value") == STATE_UNKNOWN
            assert state.attributes.get("count") == 0

    def test_change_rate(self):
        """Test min_age/max_age and change_rate."""
        now = dt_util.utcnow()
        mock_data = {
            "return_time": datetime(now.year + 1, 8, 2, 12, 23, 42, tzinfo=dt_util.UTC)
        }

        def mock_now():
            return mock_data["return_time"]

        with patch(
            "homeassistant.components.statistics.sensor.dt_util.utcnow", new=mock_now
        ):
            assert setup_component(
                self.hass,
                "sensor",
                {
                    "sensor": {
                        "platform": "statistics",
                        "name": "test",
                        "entity_id": "sensor.test_monitored",
                    }
                },
            )

            self.hass.block_till_done()
            self.hass.start()
            self.hass.block_till_done()

            for value in self.values:
                self.hass.states.set(
                    "sensor.test_monitored",
                    value,
                    {ATTR_UNIT_OF_MEASUREMENT: TEMP_CELSIUS},
                )
                self.hass.block_till_done()
                # insert the next value one minute later
                mock_data["return_time"] += timedelta(minutes=1)

            state = self.hass.states.get("sensor.test")

        assert datetime(
            now.year + 1, 8, 2, 12, 23, 42, tzinfo=dt_util.UTC
        ) == state.attributes.get("min_age")
        assert datetime(
            now.year + 1, 8, 2, 12, 23 + self.count - 1, 42, tzinfo=dt_util.UTC
        ) == state.attributes.get("max_age")
        assert self.change_rate == state.attributes.get("change_rate")

    def test_precision_0(self):
        """Test correct result with precision=0 as integer."""
        assert setup_component(
            self.hass,
            "sensor",
            {
                "sensor": {
                    "platform": "statistics",
                    "name": "test",
                    "entity_id": "sensor.test_monitored",
                    "precision": 0,
                }
            },
        )

        self.hass.block_till_done()
        self.hass.start()
        self.hass.block_till_done()

        for value in self.values:
            self.hass.states.set(
                "sensor.test_monitored",
                value,
                {ATTR_UNIT_OF_MEASUREMENT: TEMP_CELSIUS},
            )
            self.hass.block_till_done()

        state = self.hass.states.get("sensor.test")
        assert state.state == str(int(state.attributes.get("mean")))

    def test_precision_1(self):
        """Test correct result with precision=1 rounded to one decimal."""
        assert setup_component(
            self.hass,
            "sensor",
            {
                "sensor": {
                    "platform": "statistics",
                    "name": "test",
                    "entity_id": "sensor.test_monitored",
                    "precision": 1,
                }
            },
        )

        self.hass.block_till_done()
        self.hass.start()
        self.hass.block_till_done()

        for value in self.values:
            self.hass.states.set(
                "sensor.test_monitored",
                value,
                {ATTR_UNIT_OF_MEASUREMENT: TEMP_CELSIUS},
            )
            self.hass.block_till_done()

        state = self.hass.states.get("sensor.test")
        assert state.state == str(round(sum(self.values) / len(self.values), 1))

    def test_state_characteristic_unit(self):
        """Test statistics characteristic selection (via config)."""
        assert setup_component(
            self.hass,
            "sensor",
            {
                "sensor": [
                    {
                        "platform": "statistics",
                        "name": "test_min_age",
                        "entity_id": "sensor.test_monitored",
                        "state_characteristic": "min_age",
                    },
                    {
                        "platform": "statistics",
                        "name": "test_variance",
                        "entity_id": "sensor.test_monitored",
                        "state_characteristic": "variance",
                    },
                    {
                        "platform": "statistics",
                        "name": "test_average_change",
                        "entity_id": "sensor.test_monitored",
                        "state_characteristic": "average_change",
                    },
                    {
                        "platform": "statistics",
                        "name": "test_change_rate",
                        "entity_id": "sensor.test_monitored",
                        "state_characteristic": "change_rate",
                    },
                ]
            },
        )

        self.hass.block_till_done()
        self.hass.start()
        self.hass.block_till_done()

        for value in self.values:
            self.hass.states.set(
                "sensor.test_monitored",
                value,
                {ATTR_UNIT_OF_MEASUREMENT: TEMP_CELSIUS},
            )
            self.hass.states.set(
                "sensor.test_monitored_unitless",
                value,
            )
            self.hass.block_till_done()

        state = self.hass.states.get("sensor.test_min_age")
        assert state.state == str(state.attributes.get("min_age"))
        assert state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) is None
        state = self.hass.states.get("sensor.test_variance")
        assert state.state == str(state.attributes.get("variance"))
        assert state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) == TEMP_CELSIUS + "²"
        state = self.hass.states.get("sensor.test_average_change")
        assert state.state == str(state.attributes.get("average_change"))
        assert (
            state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) == TEMP_CELSIUS + "/sample"
        )
        state = self.hass.states.get("sensor.test_change_rate")
        assert state.state == str(state.attributes.get("change_rate"))
        assert state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) == TEMP_CELSIUS + "/s"

    def test_state_class(self):
        """Test state class, which depends on the characteristic configured."""
        assert setup_component(
            self.hass,
            "sensor",
            {
                "sensor": [
                    {
                        "platform": "statistics",
                        "name": "test_normal",
                        "entity_id": "sensor.test_monitored",
                        "state_characteristic": "count",
                    },
                    {
                        "platform": "statistics",
                        "name": "test_nan",
                        "entity_id": "sensor.test_monitored",
                        "state_characteristic": "min_age",
                    },
                ]
            },
        )

        self.hass.block_till_done()
        self.hass.start()
        self.hass.block_till_done()

        for value in self.values:
            self.hass.states.set(
                "sensor.test_monitored",
                value,
                {ATTR_UNIT_OF_MEASUREMENT: TEMP_CELSIUS},
            )
            self.hass.block_till_done()

        state = self.hass.states.get("sensor.test_normal")
        assert state.attributes.get(ATTR_STATE_CLASS) == STATE_CLASS_MEASUREMENT
        state = self.hass.states.get("sensor.test_nan")
        assert state.attributes.get(ATTR_STATE_CLASS) is None

    def test_unitless_source_sensor(self):
        """Statistics for a unitless source sensor should never have a unit."""
        assert setup_component(
            self.hass,
            "sensor",
            {
                "sensor": [
                    {
                        "platform": "statistics",
                        "name": "test_unitless_1",
                        "entity_id": "sensor.test_monitored_unitless",
                        "state_characteristic": "count",
                    },
                    {
                        "platform": "statistics",
                        "name": "test_unitless_2",
                        "entity_id": "sensor.test_monitored_unitless",
                        "state_characteristic": "mean",
                    },
                    {
                        "platform": "statistics",
                        "name": "test_unitless_3",
                        "entity_id": "sensor.test_monitored_unitless",
                        "state_characteristic": "change_rate",
                    },
                ]
            },
        )

        self.hass.block_till_done()
        self.hass.start()
        self.hass.block_till_done()

        for value in self.values:
            self.hass.states.set(
                "sensor.test_monitored_unitless",
                value,
            )
            self.hass.block_till_done()

        state = self.hass.states.get("sensor.test_unitless_1")
        assert state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) is None
        state = self.hass.states.get("sensor.test_unitless_2")
        assert state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) is None
        state = self.hass.states.get("sensor.test_unitless_3")
        assert state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) is None

        assert state.attributes.get(ATTR_STATE_CLASS) == STATE_CLASS_MEASUREMENT

    def test_initialize_from_database(self):
        """Test initializing the statistics from the database."""
        # enable the recorder
        init_recorder_component(self.hass)
        self.hass.block_till_done()
        self.hass.data[recorder.DATA_INSTANCE].block_till_done()
        # store some values
        for value in self.values:
            self.hass.states.set(
                "sensor.test_monitored",
                value,
                {ATTR_UNIT_OF_MEASUREMENT: TEMP_CELSIUS},
            )
            self.hass.block_till_done()
        # wait for the recorder to really store the data
        wait_recording_done(self.hass)
        # only now create the statistics component, so that it must read the
        # data from the database
        assert setup_component(
            self.hass,
            "sensor",
            {
                "sensor": {
                    "platform": "statistics",
                    "name": "test",
                    "entity_id": "sensor.test_monitored",
                    "sampling_size": 100,
                }
            },
        )

        self.hass.block_till_done()
        self.hass.start()
        self.hass.block_till_done()

        # check if the result is as in test_sensor_source()
        state = self.hass.states.get("sensor.test")
        assert str(self.mean) == state.state
        assert state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) == TEMP_CELSIUS

    def test_initialize_from_database_with_maxage(self):
        """Test initializing the statistics from the database."""
        now = dt_util.utcnow()
        mock_data = {
            "return_time": datetime(now.year + 1, 8, 2, 12, 23, 42, tzinfo=dt_util.UTC)
        }

        def mock_now():
            return mock_data["return_time"]

        # Testing correct retrieval from recorder, thus we do not
        # want purging to occur within the class itself.
        def mock_purge(self):
            return

        # Set maximum age to 3 hours.
        max_age = 3
        # Determine what our minimum age should be based on test values.
        expected_min_age = mock_data["return_time"] + timedelta(
            hours=len(self.values) - max_age
        )

        # enable the recorder
        init_recorder_component(self.hass)
        self.hass.block_till_done()
        self.hass.data[recorder.DATA_INSTANCE].block_till_done()

        with patch(
            "homeassistant.components.statistics.sensor.dt_util.utcnow", new=mock_now
        ), patch.object(StatisticsSensor, "_purge_old", mock_purge):
            # store some values
            for value in self.values:
                self.hass.states.set(
                    "sensor.test_monitored",
                    value,
                    {ATTR_UNIT_OF_MEASUREMENT: TEMP_CELSIUS},
                )
                self.hass.block_till_done()
                # insert the next value 1 hour later
                mock_data["return_time"] += timedelta(hours=1)

            # wait for the recorder to really store the data
            wait_recording_done(self.hass)
            # only now create the statistics component, so that it must read
            # the data from the database
            assert setup_component(
                self.hass,
                "sensor",
                {
                    "sensor": {
                        "platform": "statistics",
                        "name": "test",
                        "entity_id": "sensor.test_monitored",
                        "sampling_size": 100,
                        "max_age": {"hours": max_age},
                    }
                },
            )
            self.hass.block_till_done()

            self.hass.block_till_done()
            self.hass.start()
            self.hass.block_till_done()

            # check if the result is as in test_sensor_source()
            state = self.hass.states.get("sensor.test")

        assert expected_min_age == state.attributes.get("min_age")
        # The max_age timestamp should be 1 hour before what we have right
        # now in mock_data['return_time'].
        assert mock_data["return_time"] == state.attributes.get("max_age") + timedelta(
            hours=1
        )


async def test_reload(hass):
    """Verify we can reload filter sensors."""
    await hass.async_add_executor_job(
        init_recorder_component, hass
    )  # force in memory db

    hass.states.async_set("sensor.test_monitored", 12345)
    await async_setup_component(
        hass,
        "sensor",
        {
            "sensor": {
                "platform": "statistics",
                "name": "test",
                "entity_id": "sensor.test_monitored",
                "sampling_size": 100,
            }
        },
    )
    await hass.async_block_till_done()
    await hass.async_start()
    await hass.async_block_till_done()

    assert len(hass.states.async_all()) == 2

    assert hass.states.get("sensor.test")

    yaml_path = get_fixture_path("configuration.yaml", "statistics")
    with patch.object(hass_config, "YAML_CONFIG_FILE", yaml_path):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RELOAD,
            {},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert len(hass.states.async_all()) == 2

    assert hass.states.get("sensor.test") is None
    assert hass.states.get("sensor.cputest")
