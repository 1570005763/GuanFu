import xmlrpc.client


class KojiClient:
    def __init__(self, server_url):
        self.server_url = server_url
        self.session = xmlrpc.client.ServerProxy(server_url, allow_none=True)

    def get_rpm(self, rpm_info):
        return self.session.getRPM(rpm_info, True, False)

    def get_rpm_optional(self, rpm_info):
        return self.session.getRPM(rpm_info, False, False)

    def get_external_repo_list(self, tag_info, event=None):
        return self.session.getExternalRepoList(tag_info, event)

    def get_build(self, build_id):
        return self.session.getBuild(build_id, True)

    def get_buildroot(self, buildroot_id):
        return self.session.getBuildroot(buildroot_id, True)

    def get_task_children(self, task_id):
        return self.session.getTaskChildren(task_id)

    def get_task_result(self, task_id):
        return self.session.getTaskResult(task_id, False)

    def list_task_output(self, task_id):
        return self.session.listTaskOutput(task_id, True)

    def download_task_output(self, task_id, filename, offset, size):
        return self.session.downloadTaskOutput(task_id, filename, offset, size)
