### Pake: A python-powered Make-like

Pake is a tool for doing builds, similar in spirit to Make but with less complexity,
magic and special cases. It also has a content-based approach to invalidation instead
of using file timestamps.

It explicitly is not trying to compete with modern, high-power build systems like Bazel
with a focus on hermecity and correctness. It is intended for simpler use cases where you
aren't building a giant monorepo but rather a single project, and would like to continue
using the standard build tools for your project, just with a dependency management layer on top.

Pake is controlled via a `Pakefile`, which is a python file (It can also be called `Pakefile.py`)
which defines build targets. For simple cases, a declarative style based on decorators is provded:

```python
@target("foo.out", deps=["foo.in"])
def build_foo(target, deps):
	run(["mycommand", "-o", target] + deps)
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
	cmd(["diff", "-u"] + deps).stdout(target).run()
```

and declare virtual (aka. "phony") targets, which may or may not actually do anything:
```python
@virtual(deps=["foo", "bar"], default=True)
def all(deps):
	pass
```

This is equivalent to:
```
.PHONY: all
all: foo bar
```

As a Pakefile is python, you can use all the normal python language features for more advanced
use cases, such as dynamically generating targets:
```python
for name in os.listdir("."):
	if os.path.isdir(name):
		files = [os.path.join(name, subname) for subname in os.listdir(name)]
		@target(f"{name}.summary", deps=files)
		def summarize(target, deps):
			with open(target, "w") as f:
				f.write(json.dumps(deps))
```
