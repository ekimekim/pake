
"""Mechanism for printing at various verbosity levels.
Use set_verbosity to set the global verbosity level.
verbose_print(v, ...) wraps print(...) but only runs
if v <= the global verbosity level.

Also exposes color formatting, which can optionally be disabled.
"""

verbosity = 0

class Colors:
	def set(self, enabled):
		self.enabled = enabled

	def make_property(format_code):
		@property
		def color_property(self):
			return f"\x1b[{format_code}m" if self.enabled else ""

	reset = make_property("")
	bold = make_property("1")
	black = make_property("30")
	red = make_property("31")
	green = make_property("32")
	yellow = make_property("33")
	blue = make_property("34")
	purple = make_property("35")
	cyan = make_property("36")
	white = make_property("37")

color = Colors()

def set(new_verbosity, color_enabled):
	global verbosity
	verbosity = new_verbosity
	color.set(color_enabled)

def verbose_print(v, *args, **kwargs):
	if v <= verbosity:
		print(*args, **kwargs)
