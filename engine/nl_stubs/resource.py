"""A stand-in for the Unix `resource` module, for Python builds without it (WebAssembly).

The engine calls resource.getrusage(RUSAGE_SELF).ru_maxrss once per stage, only to
record peak memory in its timings. A browser has no process accounting, so this
reports 0: "not measured", never a made-up figure. Nothing else in the engine reads it.
"""
from collections import namedtuple

RUSAGE_SELF = 0
RUSAGE_CHILDREN = -1

_FIELDS = ("ru_utime", "ru_stime", "ru_maxrss", "ru_ixrss", "ru_idrss", "ru_isrss",
           "ru_minflt", "ru_majflt", "ru_nswap", "ru_inblock", "ru_oublock", "ru_msgsnd",
           "ru_msgrcv", "ru_nsignals", "ru_nvcsw", "ru_nivcsw")
struct_rusage = namedtuple("struct_rusage", _FIELDS)

IS_STUB = True


def getrusage(who=RUSAGE_SELF):
    return struct_rusage(*([0.0, 0.0] + [0] * (len(_FIELDS) - 2)))
