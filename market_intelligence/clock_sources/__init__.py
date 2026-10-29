"""Replaceable clock-health sources.

A source measures the local clock's synchronization state and returns a
bounded ``ClockSourceResult``. Only a ``healthy`` result carries the
``ClockHealthReading`` the clock evidence adapter consumes; every other
status fails closed. Thresholds come from the registry's ``ClockPolicy``;
a source never chooses one.

The initial production source is the Windows Time service
(``windows_time``). Another operating-system or authenticated time source
can be added beside it with the same result contract.
"""
