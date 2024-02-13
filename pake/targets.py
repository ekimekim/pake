
import os
from hashlib import sha256
from uuid import uuid4


"""API for declaring dependencies and build targets

A generic "target" consists of a recipe function, wrapped with some metadata:
	PRIORITY: A float which sets the ordering for how target types are searched.
		This enables eg. simple targets to always overrule pattern targets.
		If targets are tied (eg. within a target type), priority is by declaration order.
	match(filepath): Returns non-None if the target can be used to build the given filepath
	deps(match): A list of targets that must be up to date before this target can build.
		Must be passed the result of a call to match().
	run(match, deps): Runs the recipe. Assumes all dependencies are already up to date.
		Must be passed the result of a call to match() and a call to deps().
		Normal targets return the hash of the built filepath. Virtual targets may return
		other values, which must be JSONable (lists, dicts, strings, numbers, bools, None)
		and should be small. The unique() function will give you a value to return that is
		suitable to indicate "my dependents should always update if I have been updated".
	update(match):
		First ensures all its dependencies are up to date
		then calls run() if not up to date and caches it.
		Finally, it returns the result of run(), which may have been cached.
"""


def unique():
	return f"unique:{uuid4()}"


def hash_file(filepath):
	"""Hashes the contents of the given file, returning a string.
	For directories, this is the list of files in that directory.
	"""
	# Note we're intentionally following symlinks here as
	# we are generally interested in file contents.
	# If the user wants to only consider a file changed if the actual symlink pointer changes,
	# they can use a virtual target that calls readlink.
	if os.path.isdir(filepath):
		hash = sha256("\0".join(sorted(os.listdir(filepath))))
	else:
		hash = sha256()
		with open(filepath, "rb") as f:
			# stream in 64KiB chunks to avoid excessive memory usage
			for chunk in f.read(64 * 1024):
				hash.update(chunk)
	return hash.hexdigest()


def normalize_path(filepath):
	if isinstance(filepath, str):
		filepath = filepath.encode()
	# relpath normalizes components (eg. "foo//bar/.." -> "foo") and leaves us with only two
	# cases: "../PATH" and "PATH".
	path = os.path.relpath(path)
	if path.startswith("../"):
		raise ValueError(f"Target cannot be outside current directory: {filepath!r}")
	# We want paths to always have a ./ prefix as this allows us to dismabiguate them from
	# virtual targets.
	return f"./{path}"


class Target:
	def __init__(self, registry, name):
		self.registry = registry
		self.name = name

	def __repr__(self):
		return f"<{type(self).__name__}({self.name!r})"

	def update(self, filepath, match):
		deps = self.deps(match)

		inputs = {}
		for dep in deps:
			target, match = self.registry.resolve(dep)
			inputs[dep] = target.update(dep, match)

		if self.registry.needs_update(filepath, inputs):
			result = self.run(match, deps)
			self.registry.save_result(filepath, result)

		return self.registry.get_result(filepath)


class AlwaysTarget(Target):
	"""A special-cased do-nothing target which always returns a unique string,
	forcing any dependent to always be rebuilt."""
	# Is fundamental and will break things if overriden, always go first
	PRIORITY = float("-inf")

	def __init__(self, registry):
		super().__init__(self, registry, "always")

	def __repr__(self):
		return "<AlwaysTarget>"

	def match(self, filepath):
		return filepath if filepath == "always" else None

	def update(self, filepath, match):
		assert filepath == match == "always"
		return unique()


class FallbackTarget(Target):
	"""The target that is used for any filepaths that otherwise don't have a matching target.
	It returns the hash of the file if it exists, or errors otherwise.
	"""
	# Matches anything, always go last
	PRIORITY = float("inf")

	def __init__(self, registry):
		super().__init__(self, registry, "fallback")

	def __repr__(self):
		return "<FallbackTarget>"

	def match(self, filepath):
		return normalize_path(filepath)

	def deps(self, match):
		return ["always"]

	def run(self, match, deps):
		try:
			return hash_file(match)
		except FileNotFoundError:
			raise BuildException(f"{match} does not exist and there is no rule to create it")


class VirtualTarget(Target):
	"""A target that doesn't output a file, but rather some other piece of data,
	or nothing. Still obeys the normal rules for being considered up-to-date.

	NAME can be used to refer to this target as a dependency.
	If both a virtual target NAME and a file called NAME exist, "NAME" refers to the virtual target
	whereas "./NAME" refers to the file.
	"""
	PRIORITY = 0 # Lower than all file-based targets, to ensure the virtual target matches NAME first

	def __init__(self, recipe, name, deps):
		super().__init__(name)
		self.recipe = recipe
		self._deps = deps

	def match(self, filepath):
		# Note intentionally not normalizing path, must literally match
		return filepath if self.name == filepath else None

	def deps(self, match):
		return self._deps

	def run(self, match, deps):
		return self.recipe(deps)


class SimpleTarget(Target):
	"""Basic target for a fixed filepath.
	Recipe is called with filename to build and list of deps.
	"""
	PRIORITY = 10 # Prefer simple targets over pattern targets

	def __init__(self, recipe, target, deps):
		super().__init__(target)
		self.recipe = recipe
		self.target = normalize_path(target)
		self._deps = deps

	def match(self, filepath):
		filepath = normalize_path(filepath)
		return filepath if self.target == filepath else None

	def deps(self, match):
		return self._deps

	def run(self, match, deps):
		self.recipe(match, deps)
		return hash_file(match)

	def __call__(self):
		


class PatternTarget(Target):
	"""A target that builds filepaths matching a regex.
	Deps may contain pattern replacements (eg. "\1.o" where the pattern is ".*\.c").
	Keep in mind that patterns are based on whole filepaths, not just the filename.
	"""
	PRIORITY = 20

	def __init__(self, recipe, pattern, deps):
		super().__init__(pattern)
		self.recipe = recipe
		self.pattern = re.compile(pattern)
		self._deps = deps

	def match(self, filepath):
		return self.pattern.match(normalize_path(filepath))

	def deps(self, match):
		return [match.expand(dep) for dep in self._deps]

	def run(self, match, deps):
		self.recipe(match.string, deps)
		return hash_file(match.string)


def as_decorator(target_type):
	@functools.wraps(target_type)
	def 
