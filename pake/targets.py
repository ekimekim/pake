
"""API for declaring dependencies and build targets

A generic "target" consists of a recipe function, wrapped with some metadata:
	match(filepath): Returns non-None if the target can be used to build the given filepath
	deps(match): A list of targets that must be up to date before this target can build.
		Must be passed the result of a call to match().
	run(match, deps): Runs the recipe. Assumes all dependencies are already up to date.
		Must be passed the result of a call to match() and a call to deps().
		Normal targets return the hash of the built filepath. Virtual targets may return
		other values, which must be JSONable (lists, dicts, strings, numbers, bools, None)
		and should be small. The special value None indicates this target should not be considered
		up-to-date and is used to get makefile-style "phony" targets that rebuild every time.

	When a target is called, it first ensures all its dependencies are up to date
	then calls run() if not up to date. Finally, it returns the result of run(),
	which may have been cached.
"""


def hash_file(filepath):
	TODO


class Target:
	def __init__(self, registry, name):
		self.registry = registry
		self.name = name

	def __repr__(self):
		return f"<{type(self).__name__}({self.name!r})"

	def __call__(self, registry, match):
		deps = self.deps(match)

		for dep in deps:
			target =


class SingleTarget(Target):
	"""Basic target for a fixed filepath.
	Recipe is called with filename to build and list of deps.
	"""
	def __init__(self, recipe, target, deps):
		super().__init__(target)
		self.recipe = recipe
		self.target = target
		self._deps = deps

	def match(self, filepath):
		filepath = normalize_path(filepath)
		return filepath if self.target == filepath else None

	def deps(self, match):
		return self._deps

	def run(self, match, deps):
		self.recipe(match, deps)
		return hash_file(match)


class PatternTarget(Target):
	"""A target that builds filepaths matching a regex.
	Deps may contain pattern replacements (eg. "\1.o" where the pattern is ".*\.c").
	Keep in mind that patterns are based on whole filepaths, not just the filename.
	"""
	def __init__(self, recipe, pattern, deps):
		super().__init__(pattern)
		self.recipe = recipe
		self.pattern = re.compile(pattern)
		self._deps = deps

	def match(self, filepath):
		return self.pattern.match(normalize_path(filepath))

	def deps(self, match):
		return [match.expand(dep) for dep in self._deps]

	def __call__(self, match, deps):
		self.recipe(match.string, deps)
		return hash_file(match.string)


class VirtualTarget(Target):
	"""A target that doesn't output a file, but rather some other piece of data,
	or nothing. Still obeys the normal rules for being considered up-to-date,
	with the exception that returning None causes it to never be considered up-to-date.

	NAME can be used to refer to this target as a dependency.
	If both a virtual target NAME and a file called NAME exist, "NAME" refers to the virtual target
	whereas "./NAME" refers to the file.
	"""
	def __init__(self, recipe, name, deps):
		super().__init__(name)
		self.recipe = recipe
		self._deps = deps

	def match(self, filepath):
		# Note intentionally not normalizing path, must literally match
		return filepath if self.name == filepath else None

	def deps(self, match):
		return self._deps

	def __call__(self, match, deps):
		return self.recipe(deps)
