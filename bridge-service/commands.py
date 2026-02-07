"""RS-232 command constants for McIntosh MA-352."""

# Commands follow (CMD [ZONE] [PARAMS]) format per McIntosh RS-232 spec.
# This MA-352 reports and accepts the short form for common controls.
# Short-form commands (no zone)
POWER_ON_SHORT = "(PWR 1)"
POWER_OFF_SHORT = "(PWR 0)"

MUTE_ON_SHORT = "(MUT 1)"
MUTE_OFF_SHORT = "(MUT 0)"

VOLUME_SET_SHORT = "(VOL {level})"

INPUT_SET_SHORT = "(INP {value})"

# Zone-form commands (include zone)
POWER_ON_ZONE = "(PON {zone})"
POWER_OFF_ZONE = "(POF {zone})"

MUTE_ON_ZONE = "(MUT {zone} 1)"
MUTE_OFF_ZONE = "(MUT {zone} 0)"

VOLUME_SET_ZONE = "(VST {zone} {level})"

INPUT_SET_ZONE = "(INP {zone} {value})"

HELP = "(HLP)"

QUERY = "(QRY)"
