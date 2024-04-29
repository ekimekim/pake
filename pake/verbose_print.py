
"""Mechanism for printing at various verbosity levels.
Use set_verbosity to set the global verbosity level.
verbose_print(v, ...) wraps print(...) but only runs
if v <= the global verbosity level.
"""

verbosity = 0

def set_verbosity(value):
	global verbosity
	verbosity = value

def verbose_print(v, *args, **kwargs):
	if v <= verbosity:
		print(*args, **kwargs)
