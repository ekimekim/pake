
"""Mechanism for printing at various verbosity levels.
Use set_verbosity to set the global verbosity level.
verbose_print(v, text) wraps print(text) but only runs
if v <= the global verbosity level.

Also exposes color formatting, which can optionally be disabled.
"""

import re
import sys

verbosity = 0

class Colors:
	def set(self, enabled):
		self.enabled = enabled

	def make_method(format_code):
		def color_method(self, text):
			return f"\x1b[{format_code}m{text}\x1b[m" if self.enabled else text
		return color_method

	bold = make_method("1")
	black = make_method("30")
	red = make_method("31")
	green = make_method("32")
	yellow = make_method("33")
	blue = make_method("34")
	purple = make_method("35")
	cyan = make_method("36")
	white = make_method("37")

color = Colors()

def set(new_verbosity, color_enabled):
	global verbosity
	verbosity = new_verbosity
	color.set(color_enabled)

def verbose_print(v, text, file=sys.stdout):
	if v <= verbosity:
		if color.enabled:
			text = stack_colors(text)
		print(text, file=file)

def stack_colors(input):
	"""Takes some text containing SGI escapes and restructures them so that each reset
	escape restores the previous context instead of resetting completely.
	So eg. "{red} foo {blue} bar {reset} baz" would show foo in red, bar in blue,
	then baz in red instead of default."""
	output = ""
	stack = []
	while True:
		# Scan for next escape
		escape = re.search("\x1b\\[([0-9;]*)m", input)
		if escape is None:
			# No more escapes, output the remainder and exit
			output += input
			break
		# Output everything until the next escape, store everything after it for later processing
		output += input[:escape.start()]
		input = input[escape.end():]
		code = escape.group(1)
		if code == "":
			# Restore previous context if any (otherwise just preserve the reset)
			stack.pop()
			code = stack[-1] if stack else ""
		else:
			# Otherwise output escape as-is and add to context
			stack.append(code)
		output += f"\x1b[{code}m"
	return output
