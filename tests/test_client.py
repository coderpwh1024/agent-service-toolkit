from client import AgentClient, AgentClientError
from schema import ThreadSummary, UserThreads, UserThreadsInput


def test_client_package_exports_public_api() -> None:
    assert AgentClient.__name__ == "AgentClient"
    assert AgentClientError.__name__ == "AgentClientError"


def test_user_threads_schema() -> None:
    request = UserThreadsInput(user_id="user-1")
    response = UserThreads(
        threads=[ThreadSummary(thread_id="thread-1", agent_id="research-assistant")]
    )

    assert request.limit == 20
    assert response.threads[0].thread_id == "thread-1"
