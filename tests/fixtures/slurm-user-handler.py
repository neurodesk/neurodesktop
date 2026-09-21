# UserFetchHandler from NERSC/jupyterlab-slurm at
# 8dccb39808f8a1b77712a9a5773a7d2601a56683 (BSD-3-Clause).
class UserFetchHandler(APIHandler):
    def initialize(self, log=logger):
        super().initialize()
        self._serverlog = log
        self._serverlog.info("UserFetchHandler.initialize()")

    @tornado.web.authenticated
    def get(self):
        try:
            # Prefer the authenticated Jupyter identity (works correctly under
            # multi-user deployments, e.g. JupyterHub, where the OS process
            # `USER` env var may be shared, unset, or belong to a service
            # account rather than the actual signed-in user). Fall back to the
            # process `USER` only for single-user/local deployments where no
            # Jupyter identity is configured.
            username = None
            current_user = getattr(self, "current_user", None)
            if current_user is not None:
                username = getattr(current_user, "username", None) or getattr(current_user, "name", None)
                if username is None and isinstance(current_user, str):
                    username = current_user
            if not username:
                username = os.environ.get('USER')
            self._serverlog.info("UserFetchHandler.get() {}".format(username))
            self.finish(json.dumps(make_envelope(True, data={"user": username})))
        except Exception as e:
            self._serverlog.exception(e)
            self.set_status(500)
            self.finish(json.dumps(make_envelope(False, error=str(e), exit_code=1)))

