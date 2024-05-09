# Pake: A python-powered Make-like

Pake is a tool for doing builds, similar in spirit to Make but with less complexity,
magic and special cases. It also has a content-based approach to invalidation instead
of using file timestamps.

It explicitly is not trying to compete with modern, high-power build systems like Bazel
with a focus on hermecity and correctness. It is intended for simpler use cases where you
aren't building a giant monorepo but rather a single project, and would like to continue
using the standard build tools for your project, just with a dependency management layer on top.

## Basics

Pake is controlled via a `Pakefile`, which is a python file (It can also be called `Pakefile.py`)
which defines build targets. In the simplest case, you specify a target filename, what other targets
it depends on, and some python code to be run when it should be built:

```python
@target("foo.out", deps=["foo.in"])
def build_foo(target, deps):
	cmd("mycommand", "-o", target, *deps).run()
```

This is approximately equivalent to the following Makefile rule:
```
foo.out: foo.in
	mycommand -o $@ $*
```

You can also use regexes to define pattern rules:
```python
@pattern(r"(.*)\.diff", deps=[r"\1.old", r"\1.new"])
def make_diff(target, deps):
	cmd("diff", "-u", *deps).stdout(target).run()
```

This rule will be used for any target ending in `.diff`.

Note in these examples we are using the provided `cmd()` helper to run commands,
but you also have the whole python language at your disposal and can do whatever you like.

To run your pakefile, use the `pake` command, specifying one or more targets:

```
$ pake TARGET
```

Or run with no targets to build the `default` target, which is generally an alias for one or more
other targets.

Each target will be checked and built if not already up to date.

## How it works

When `pake` checks a target is up to date, it does the following:

1. Finds the matching *rule* that specifies how to build it
2. Checks all its dependencies are up to date
3. For non-virtual rules, hashes the file to check if it has been modified since last build
4. If the file or any of its dependencies has changed, build it by executing the rule's *recipe*.

Whether a file "has changed" is determined by a hash of the file contents
(or for virtual rules, the returned value - see below).
For directories, a sorted list of filenames in the directory is used.

Hashes are kept in a state file, called `.pake-state` by default.

### Virtual rules

Not all targets correspond to a file. Some use cases are:
1. A target which is an alias for a group of other targets.
   This need not do anything except trigger its dependencies.
2. A target which performs some task outside the context of files on disk,
   such as uploading a built file. This should run whenever its dependents change.
3. A target which gathers some information from the environment, to be used as an input to other targets.
   That way if the environment information changes, all dependent targets are invalidated.

To support these use-cases, rules have a concept of a *result value*.
For file-based (ie. non-virtual) rules, this is a hash of the file contents as specified above.

For virtual rules, this can be any JSON-encodable value, but it is recommended to keep it small and simple.
A rule that depends on a virtual rule will be rebuilt only if the virtual rule's result changes.

To return to the above example list, for each use-case you would use a result:
1. A combination of the results of all your dependencies, effectively "passing through" changes.
   This is done for you by the built-in `alias()` and `group()` rule constructors.
2. If running the rule only updates an external system and is not used as an input to any other recipe,
   it can just return `None`. Its dependents will never consider it as changed.
3. The target's result would be the information gathered from the environment,
   so dependents would be rebuilt if the value changes.

## Rule types

As mentioned above, all targets are built by being matched with a *rule*.
A rule specifies:
- What targets it matches
- What its dependencies are
- When it should be built
- How to build the target (a *recipe*, in the form of a python function)
- What the result value is

Rules come in several types.

### Target Rule

The simplest form of rule, this matches a single filepath and has a fixed list of dependencies.
It will match the filepath even if given as a non-normalized path, ie. `foo.txt`, `./foo.txt`
and `bar/../foo.txt` will all match a filepath `foo.txt`. However targets must be within
the top-level directory where `pake` is run (ie. `../foo.txt` is not a valid file target).

Target rules take a recipe function which takes two arguments:
- The normalized target name with a leading `./`, eg. `./foo.txt`.
  This is useful to avoid having to re-type the target filepath.
- A dictionary mapping dependencies to their result value.
  The ordering of items in the dictionary will match the order in which the dependencies were specified.
  This can be used (by listing the keys) as a list of input files,
  or to look up some specific input value.

It is an error for the target file to not exist after returning from the recipe.
Raise an exception if you need to indicate failure.

Examples:

```
# No dependencies
@target("helloworld.txt")
def hello_world(target, deps):
	write(target, "Hello, World!")

# Using dependency list
@target("textarchive.tar", ["helloworld.txt", "otherfile.txt"])
def text_archive(target, deps):
	cmd("tar", "cf", target, *deps).run()

# Using dependency values
@target("build_info.json", deps=["textarchive.tar", "git_tag"])
def build_info(target, deps):
	write(target, json.dumps({
		"hash": deps["textarchive.tar"],
		"tag": deps["git_tag"],
	})
```

### Pattern Rule

A more general form of a target rule, it takes a regex that matches a filepath.
Dependencies can contain substitutions using the matched regex groups.
This lets you specify a general way to build all files of a certain type.

The match regex must match the entire normalized filepath, though it may ignore the
leading `./` that is added when normalizing filepaths. For example, `.*\.txt` will match
all files that end in `.txt` including in subdirectories. To only match things in the top-level
directory you could write `[^/]*\.txt`.

Pattern rule recipes take the same `target` and `deps` arguments as target rule recipes,
but also take a third `match` argument which is the `re.Match` object obtained by matching
on the target. This is useful for extracting groups from the match for use in the recipe.

If a target rule and a pattern rule both match the same target, the target rule takes precedence.
If two pattern rules match, the one defined first takes precedence.

Examples:

```
# Fixed dependencies
@pattern(".*/marker", ["marker-contents.txt"])
def marker(target, deps, match):
	shutil.copy("marker-contents.txt", target)

# Substituted dependencies
@pattern(r"(.*)\.o", [r"\1.c", r"\1.h"])
def compile(target, deps, match):
	cmd("cc", "-a", target, *deps).run()

# Using match groups
@pattern("(.*)/([^/]*).json")
def self_referential_json(target, deps, match):
	directory, name = match.groups()
	write(target, json.dumps({
		"directory": directory,
		"name": name,
	})
```

### Fallback Rule

If a target does not match any rule, it matches the "fallback" rule.
This rule is intended for "source" files that cannot be built but may be used
as dependencies.

All it does is hash the file and return that result if it exists.
If the file does not exist it reports an error:

```
$ pake does-not-exist.txt
./does-not-exist.txt: File does not exist and there is no rule to create it
```

### Virtual Rule

Virtual rules do not match files, but instead arbitrary target names such as `install` or `default`.
In `make` parlance these would be called `PHONY` targets, but there are some important differences:
* `PHONY` targets are always rebuilt. Virtual targets are rebuilt only if their dependencies change
  (you can depend on the `always` target to counteract this - see below).
* `PHONY` targets cause their dependents to always be rebuilt. Dependents of virtual targets are only
  rebuilt if the virtual target's result value changes.

A virtual rule has a name which is the target string to match. By convention these should be
simple identifiers, and by default the name of the recipe function is used. This can be overriden
with the `name` argument. A virtual rule only matches the exact target string, and takes precedence
over any file-based rule.

Virtual rules take a recipe function which takes only a `deps` dictionary
(as per target rule's second arg).

This recipe function's return value is used as the result of the target.
This result value must be encodable as JSON via the standard `json.dumps()`,
and it is acceptable to not return anything explicitly and thus return `None`.

If you would like to force your dependents to always be rebuilt if you are,
the `registry.unique()` helper will return a suitable unique-per-invocation value.

Examples:

```
# Virtual targets follow the normal rebuild rules, so this recipe will only be run
# when foo.txt changes.
@virtual(["foo.txt"])
def upload_foo(deps):
	cmd("scp", "foo.txt", "somehost:foo.txt").run()

# Extract just a file listing from an archive so that other targets can depend on it
# and only be rebuilt if the listing changes, not if any of the file contents in the archive change.
@virtual(["archive.tar"])
def archive_file_list(deps):
	files = cmd("tar", "tf", *deps).get_output()
	return sorted(files.split("\n"))

# Use a target name that isn't a valid python function name
@virtual(name="foo:bar")
def foo_bar(deps):
	return "foobar"
```

#### Target rule vs virtual rule matching

Since target rules match any version of the filename, it is still possible to match a file
with the name of a target rule.

```
@target("foo")
def foo_file(target, deps):
	...

@virtual()
def foo(deps):
	...
```

In this example, the target `foo` would run the `foo` function
but the target `./foo` would run the `foo_file` function.

Needless to say, this kind of ambiguity is confusing and not recommended!
But it may be helpful when dealing with pattern rules and wanting to avoid conflicts.

### Alias rules

These are a special case of virtual rule which allows aliasing a target name to one
or more other targets. Depending on that target is then equivalent to depending
on all the targets it aliases to.

Two helper functions are available for this use case:
* `alias(name, target)` for aliasing to a single other target
* `group(name, targets)` for aliasing to a group of other targets.

`alias(name, target)` is equivalent to `group(name, [target])` and is provided solely for readability
over the latter.

Examples:

```
alias("foo", "foo.txt")

group("all", ["bin/debug/foo", "bin/release/foo", "bin/debug/bar", "bin/release/bar"])
```

## Special targets

The following target names are special and have a pre-defined meaning.

### `default`

This is the target that will be built if no target is specified on the command line.

Generally this will be an alias for another target or group of targets.
However it is not required to define this target, in which case failing to specify a target
will result in an error.

As a shortcut, you can alias `default` to another target using the `@default` decorator on the rule.
Note that this won't work properly on a pattern rule, since there isn't a single target
associated with the rule.

```
# Defining an alias using the @default decorator
@default
@target("output.txt")
def output(target, deps):
	...

# Defining an alias group
group("default", ["bins", "libs", "data"])

# Defining the target explicitly
@virtual(["foo", "bar"])
def default(deps):
	print("Built both foo and bar by default")
```

### `always`

The `always` target is always considered to have changed every time `pake` is run.

By specifying it as a dependency, you can require that your target is always rebuilt.
This is mostly useful for virtual rules, eg. that gather information from the environment
or run some command when explicitly requested by a user.

As a shortcut, you can create a virtual rule with `always` as a dependency via the `@always` wrapper:

```
@always()
def git_tag(deps):
	return cmd("git", "rev-parse", "HEAD").get_output()
```

`@always` acts exactly like `@virtual` except it implicitly appends `always` to the dependency list.
