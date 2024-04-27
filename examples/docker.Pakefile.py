
"""This is an example of using pake to build and push a docker image under the "docker" directory,
tagged with the current git commit."""

docker = cmd("docker")
git = cmd("git")

IMAGE_DIR = "docker"
REPO = "example.com/myimage"

# Generate the image tag from the current commit
@always()
def tag(deps):
	commit = git("rev-parse", "HEAD").get_output()
	return f"{REPO}:{commit}"

# find() will list all files in the image directory, causing a rebuild if any of them change.
# Returns the image id.
@virtual(deps=find(IMAGE_DIR))
def build(deps):
	return docker("build", "-q", IMAGE_DIR).get_output()

# Push the image if either it has changed or the tag has.
# Returns None since this is only executing for side effects.
@default
@virtual(deps=["build", "tag"])
def push(deps):
	image_id = deps["build"]
	tag = deps["tag"]
	docker("tag", image_id, tag).run()
	docker("push", tag).run()
