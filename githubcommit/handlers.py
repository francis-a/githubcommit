from notebook.utils import url_path_join as ujoin
from notebook.base.handlers import IPythonHandler
import os, json, git, urllib, requests
from git import Repo, GitCommandError
from subprocess import check_output
from sys import stderr
import pwd


def print_err(string):
    print(string, file=stderr)


def get_username():
    return pwd.getpwuid(os.getuid())[0]


class GitCommitHandler(IPythonHandler):

    def error_and_return(self, dirname, reason):
        # send error
        self.send_error(500, reason=reason)
        # return to directory
        os.chdir(dirname)

    def put(self):

        try:
            print_err("Running as user {}".format(get_username()))
            # git parameters from environment variables
            # expand variables since Docker's will pass VAR=$VAL as $VAL without expansion
            print_err("Pre dir {}, {}".format(os.environ.get('GIT_PARENT_DIR'),
                                              os.path.expandvars(os.environ.get('GIT_REPO_NAME'))))
            git_dir = "{}/{}".format(os.path.expandvars(os.environ.get('GIT_PARENT_DIR')),
                                     os.path.expandvars(os.environ.get('GIT_REPO_NAME')))
            print_err("Git dir: {}".format(git_dir))
            git_url = os.path.expandvars(os.environ.get('GIT_REMOTE_URL'))
            print_err("Remote url: {}".format(git_url))
            git_user = os.path.expandvars(os.environ.get('GIT_USER'))
            print_err("User: {}".format(git_user))
            git_repo_upstream = os.path.expandvars(os.environ.get('GIT_REMOTE_UPSTREAM'))
            print_err("Repo upstream: {}".format(git_repo_upstream))
            git_branch = git_remote = os.path.expandvars(os.environ.get('GIT_BRANCH_NAME'))
            print_err("Branch: {}".format(git_branch))
            git_access_token = os.path.expandvars(os.environ.get('GITHUB_ACCESS_TOKEN'))
            print_err("Token: {}".format(git_access_token))

            # get the parent directory for git operations
            git_dir_parent = os.path.dirname(git_dir)
            print_err("Parent dir {}".format(git_dir_parent))

            # obtain filename and msg for commit
            data = json.loads(self.request.body.decode('utf-8'))
            print_err("Loaded json: {}".format(data))
            filename = urllib.parse.unquote(data['filename'])
            print_err("Filename: {}".format(filename))
            msg = data['msg']

            # get current directory (to return later)
            cwd = os.getcwd()
            print_err("working dir {}".format(cwd))

            # select branch within repo
            try:
                os.chdir(git_dir)
                dir_repo = check_output(['git', 'rev-parse', '--show-toplevel']).strip()
                repo = Repo(dir_repo.decode('utf8'))
            except GitCommandError as e:
                self.error_and_return(cwd, "Could not checkout repo: {}".format(dir_repo))
                return

            # create new branch
            try:
                print_err(repo.git.checkout('HEAD', b=git_branch))
            except GitCommandError:
                print_err("Switching to {}".format(repo.heads[git_branch].checkout()))

            # commit current notebook
            # client will sent pathname containing git directory; append to git directory's parent
            try:
                command = str(os.environ.get('GIT_PARENT_DIR') + "/" + os.environ.get('GIT_REPO_NAME') + filename)
                print_err("Will add {}".format(command))
                print_err(repo.git.add(
                    str(os.environ.get('GIT_PARENT_DIR') + "/" + os.environ.get('GIT_REPO_NAME') + filename)))
                print_err(repo.git.commit(a=True, m="{}\n\nUpdated {}".format(msg, filename)))
            except GitCommandError as e:
                print_err(e)
                self.error_and_return(cwd, "Could not commit changes to notebook: {}".format(git_dir_parent + filename))
                return

            # create or switch to remote
            try:
                remote = repo.create_remote(git_remote, git_url)
            except GitCommandError:
                print_err("Remote {} already exists...".format(git_remote))
                remote = repo.remote(git_remote)

            # push changes
            try:
                remote.pull()
                pushed = remote.push(git_branch)
                assert len(pushed) > 0
                assert pushed[0].flags in [git.remote.PushInfo.UP_TO_DATE, git.remote.PushInfo.FAST_FORWARD,
                                           git.remote.PushInfo.NEW_HEAD, git.remote.PushInfo.NEW_TAG]
            except GitCommandError as e:
                print_err(e)
                self.error_and_return(cwd, "Could not push to remote {}".format(git_remote))
                return
            except AssertionError as e:
                self.error_and_return(cwd, "Could not push to remote {}: {}".format(git_remote, pushed[0].summary))
                return

            # open pull request
            try:
                github_url = "https://api.github.com/repos/{}/pulls".format(git_repo_upstream)
                github_pr = {
                    "title": "{} Notebooks".format(git_user),
                    "body": "IPython notebooks submitted by {}".format(git_user),
                    "head": "{}:{}".format(git_user, git_remote),
                    "base": "master"
                }
                github_headers = {"Authorization": "token {}".format(git_access_token)}
                r = requests.post(github_url, data=json.dumps(github_pr), headers=github_headers)
                if r.status_code != 201:
                    print_err("Error submitting Pull Request to {}".format(git_repo_upstream))
            except:
                print_err("Error submitting Pull Request to {}".format(git_repo_upstream))

            # return to directory
            os.chdir(cwd)

            # close connection
            self.write({'status': 200,
                        'statusText': 'Success!  Changes to {} captured on branch {} at {}'.format(filename, git_branch,
                                                                                                   git_url)})
        except Exception as e:
            print_err(e)
            cwd = os.getcwd()
            self.error_and_return(cwd, e)


def setup_handlers(nbapp):
    route_pattern = ujoin(nbapp.settings['base_url'], '/git/commit')
    nbapp.add_handlers('.*', [(route_pattern, GitCommitHandler)])
