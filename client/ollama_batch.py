"""WeRU.B Ollama Batch Client — Python SDK for Ollama Admin API."""

import json
import time
import requests


class OllamaBatch:
    """Ollama Admin API client.

    Usage:
        client = OllamaBatch("https://example.org/ollama", api_key="sk-...")

        # Single job
        job = client.submit("qwen2.5:7b", "Translate: 안녕하세요")
        result = job.wait()
        print(result["response"])

        # Batch
        batch = client.batch("llama3.2:3b", [
            {"prompt": "What is 1+1?"},
            {"prompt": "What is 2+2?"},
        ])
        results = batch.wait()

        # Status
        print(client.status())
        print(client.models())
    """

    def __init__(self, base_url="http://localhost:8585", api_key=None, timeout=30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        if api_key:
            self.session.headers["Authorization"] = f"Bearer {api_key}"

    def _get(self, path, **kwargs):
        r = self.session.get(f"{self.base_url}{path}", timeout=self.timeout, **kwargs)
        r.raise_for_status()
        return r.json()

    def _post(self, path, data=None, **kwargs):
        r = self.session.post(f"{self.base_url}{path}", json=data, timeout=self.timeout, **kwargs)
        r.raise_for_status()
        return r.json()

    # --- Queue ---

    def submit(self, model, prompt, system=None, priority=5, options=None,
               metadata=None, webhook_url=None):
        """Submit a single job. Returns a Job object."""
        payload = {"model": model, "prompt": prompt, "priority": priority}
        if system:
            payload["system"] = system
        if options:
            payload["options"] = options
        if metadata:
            payload["metadata"] = metadata
        if webhook_url:
            payload["webhook_url"] = webhook_url
        data = self._post("/api/queue/submit", payload)
        return Job(self, data["job_id"])

    def batch(self, model, jobs, system=None, priority=5, options=None):
        """Submit a batch. Returns a Batch object.

        Args:
            model: Default model for all jobs
            jobs: List of dicts with 'prompt' (required), optionally 'model', 'system', 'options'
        """
        payload = {"model": model, "jobs": jobs, "priority": priority}
        if system:
            payload["system"] = system
        if options:
            payload["options"] = options
        data = self._post("/api/queue/batch", payload)
        return Batch(self, data["batch_id"], data["count"])

    def batch_upload(self, filepath, model=None, system=None):
        """Upload a JSONL file as batch. Returns a Batch object."""
        with open(filepath, "rb") as f:
            files = {"file": f}
            form = {}
            if model:
                form["model"] = model
            if system:
                form["system"] = system
            r = self.session.post(
                f"{self.base_url}/api/queue/batch/upload",
                files=files, data=form, timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()
        return Batch(self, data["batch_id"], data["count"])

    def job(self, job_id):
        """Get job status."""
        return self._get(f"/api/queue/job/{job_id}")

    def batch_status(self, batch_id):
        """Get batch status with all jobs."""
        return self._get(f"/api/queue/batch/{batch_id}")

    def cancel(self, job_id):
        """Cancel a pending job."""
        return self._post(f"/api/queue/cancel/{job_id}")

    def retry(self, job_id):
        """Retry a failed job."""
        return self._post(f"/api/queue/retry/{job_id}")

    def pause(self):
        """Pause queue processing."""
        return self._post("/api/queue/pause")

    def resume(self):
        """Resume queue processing."""
        return self._post("/api/queue/resume")

    def purge(self, before=None):
        """Delete completed/failed jobs."""
        payload = {"before": before} if before else {}
        return self._post("/api/queue/purge", payload)

    # --- Status ---

    def status(self):
        """Get queue counts and worker status."""
        return self._get("/api/queue/status")

    def stats(self):
        """Get performance statistics."""
        return self._get("/api/queue/stats")

    def search(self, status=None, model=None, q=None, job_id=None, limit=50, offset=0):
        """Search jobs with filters."""
        params = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        if model:
            params["model"] = model
        if q:
            params["q"] = q
        if job_id:
            params["job_id"] = job_id
        return self._get("/api/queue/jobs", params=params)

    # --- Models ---

    def models(self):
        """List installed models."""
        return self._get("/api/models")

    def ollama_status(self):
        """Get Ollama engine status."""
        return self._get("/api/status")


class Job:
    """Represents a submitted job."""

    def __init__(self, client, job_id):
        self.client = client
        self.id = job_id
        self._result = None

    def poll(self):
        """Get current job status."""
        self._result = self.client.job(self.id)
        return self._result

    @property
    def done(self):
        if self._result is None:
            self.poll()
        return self._result["status"] in ("completed", "failed")

    def wait(self, poll_interval=2, timeout=600):
        """Block until job completes. Returns job dict."""
        start = time.time()
        while True:
            self.poll()
            if self.done:
                return self._result
            if time.time() - start > timeout:
                raise TimeoutError(f"Job {self.id} timed out after {timeout}s")
            time.sleep(poll_interval)

    @property
    def response(self):
        if self._result and self._result["status"] == "completed":
            return self._result.get("response", "")
        return None

    def __repr__(self):
        s = self._result["status"] if self._result else "unknown"
        return f"<Job {self.id[:8]} status={s}>"


class Batch:
    """Represents a submitted batch."""

    def __init__(self, client, batch_id, count=0):
        self.client = client
        self.id = batch_id
        self.count = count
        self._result = None

    def poll(self):
        """Get current batch status."""
        self._result = self.client.batch_status(self.id)
        return self._result

    @property
    def done(self):
        if self._result is None:
            self.poll()
        return (self._result.get("pending", 0) + self._result.get("running", 0)) == 0

    def wait(self, poll_interval=3, timeout=3600):
        """Block until all jobs complete. Returns batch dict."""
        start = time.time()
        while True:
            self.poll()
            if self.done:
                return self._result
            if time.time() - start > timeout:
                raise TimeoutError(f"Batch {self.id} timed out after {timeout}s")
            time.sleep(poll_interval)

    @property
    def jobs(self):
        if self._result:
            return self._result.get("jobs", [])
        return []

    def results_ndjson(self):
        """Download results as NDJSON string."""
        r = self.client.session.get(
            f"{self.client.base_url}/api/queue/batch/{self.id}/results",
            timeout=self.client.timeout,
        )
        r.raise_for_status()
        return r.text

    def __repr__(self):
        if self._result:
            return f"<Batch {self.id[:8]} {self._result.get('completed',0)}/{self._result.get('total',self.count)}>"
        return f"<Batch {self.id[:8]} count={self.count}>"
