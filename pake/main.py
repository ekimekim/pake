
import sys
import os
import traceback

from .registry import Registry
from .rules import FallbackRule
from .exceptions import PakeError

def main(*targets, pakefile=None, statefile=".pake-state", force=False):
	try:
		if pakefile is None:
			candidates = ["Pakefile", "Pakefile.py"]
			for candidate in candidates:
				if os.path.exists(candidate):
					pakefile = candidate
					break
			else:
				raise PakeError("Could not find Pakefile, are you in the right directory?")

		registry = Registry(statefile)
		registry.load_pakefile(pakefile)

		if not targets:
			default_rule, match = registry.resolve("default")
			if isinstance(default_rule, FallbackRule):
				raise PakeError("No targets given and no default target defined.")
			targets = ["default"]

		for target in targets:
			registry.update(target, force=force)

	except PakeError as e:
		print(e, file=sys.stderr)
		if e.__cause__ is not None:
			traceback.print_exception(e.__cause__)
		sys.exit(1)
