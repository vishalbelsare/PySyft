# stdlib
import asyncio
from enum import auto
import random

# third party
from faker import Faker
import pytest
from sim.core import BaseEvent
from sim.core import Simulator
from sim.core import SimulatorContext
from sim.core import sim_activity
from sim.core import sim_entrypoint

# syft absolute
import syft as sy
from syft import test_settings
from syft.client.client import SyftClient
from syft.util.test_helpers.apis import make_schema
from syft.util.test_helpers.apis import make_test_query
from syft.util.test_helpers.worker_helpers import (
    build_and_launch_worker_pool_from_docker_str,
)

fake = Faker()


class Event(BaseEvent):
    # overall state
    INIT = auto()
    ADMIN_LOWSIDE_FLOW_COMPLETED = auto()
    ADMIN_HIGHSIDE_FLOW_COMPLETED = auto()
    USER_FLOW_COMPLETED = auto()
    # admin - endpoints
    ADMIN_ALL_ENDPOINTS_CREATED = auto()
    ADMIN_BQ_TEST_ENDPOINT_CREATED = auto()
    ADMIN_BQ_SUBMIT_ENDPOINT_CREATED = auto()
    ADMIN_BQ_SCHEMA_ENDPOINT_CREATED = auto()
    ADMIN_LOW_SIDE_ENDPOINTS_AVAILABLE = auto()
    # admin - worker pool
    ADMIN_WORKER_POOL_CREATED = auto()
    ADMIN_LOWSIDE_WORKER_POOL_CREATED = auto()
    ADMIN_HIGHSIDE_WORKER_POOL_CREATED = auto()
    # admin sync
    ADMIN_SYNC_COMPLETED = auto()
    ADMIN_SYNCED_HIGH_TO_LOW = auto()
    ADMIN_SYNCED_LOW_TO_HIGH = auto()
    # users
    GUEST_USERS_CREATED = auto()
    USER_CAN_QUERY_TEST_ENDPOINT = auto()
    USER_CAN_SUBMIT_QUERY = auto()


# ------------------------------------------------------------------------------------------------


def query_sql():
    dataset_2 = test_settings.get("dataset_2", default="dataset_2")
    table_2 = test_settings.get("table_2", default="table_2")
    table_2_col_id = test_settings.get("table_2_col_id", default="table_id")
    table_2_col_score = test_settings.get("table_2_col_score", default="colname")

    query = f"SELECT {table_2_col_id}, AVG({table_2_col_score}) AS average_score \
        FROM {dataset_2}.{table_2} \
        GROUP BY {table_2_col_id} \
        LIMIT 10000"
    return query


def get_code_from_msg(msg: str):
    return str(msg.split("`")[1].replace("()", "").replace("client.", ""))


# ------------------------------------------------------------------------------------------------


@sim_activity(
    wait_for=Event.ADMIN_LOW_SIDE_ENDPOINTS_AVAILABLE,
    trigger=Event.USER_CAN_QUERY_TEST_ENDPOINT,
)
async def user_query_test_endpoint(ctx: SimulatorContext, client: sy.DatasiteClient):
    """Run query on test endpoint"""

    user = client.logged_in_user

    def _query_endpoint():
        ctx.logger.info(f"User: {user} - Calling client.api.bigquery.test_query (mock)")
        res = client.api.bigquery.test_query(sql_query=query_sql())
        assert len(res) == 10000
        ctx.logger.info(f"User: {user} - Received {len(res)} rows")

    await asyncio.to_thread(_query_endpoint)


@sim_activity(
    wait_for=Event.USER_CAN_QUERY_TEST_ENDPOINT,
    trigger=Event.USER_CAN_SUBMIT_QUERY,
)
async def user_bq_submit(ctx: SimulatorContext, client: sy.DatasiteClient):
    """Submit query to be run on private data"""
    user = client.logged_in_user

    def _submit_endpoint():
        ctx.logger.info(
            f"User: {user} - Calling client.api.services.bigquery.submit_query"
        )
        res = client.api.bigquery.submit_query(
            func_name="invalid_func",
            query=query_sql(),
        )
        ctx.logger.info(f"User: {user} - Received {res}")

    await asyncio.to_thread(_submit_endpoint)


@sim_activity(wait_for=Event.GUEST_USERS_CREATED, trigger=Event.USER_FLOW_COMPLETED)
async def user_flow(ctx: SimulatorContext, server_url_low: str, user: dict):
    client = sy.login(
        url=server_url_low,
        email=user["email"],
        password=user["password"],
    )
    ctx.logger.info(f"User: {client.logged_in_user} - logged in")

    await user_query_test_endpoint(ctx, client)
    await user_bq_submit(ctx, client)


# ------------------------------------------------------------------------------------------------


@sim_activity(trigger=Event.GUEST_USERS_CREATED)
async def admin_signup_users(
    ctx: SimulatorContext, admin_client: SyftClient, users: list[dict]
):
    for user in users:
        ctx.logger.info(f"Admin: Creating guest user {user['email']}")
        admin_client.register(
            name=user["name"],
            email=user["email"],
            password=user["password"],
            password_verify=user["password"],
        )


@sim_activity(trigger=Event.ADMIN_BQ_SCHEMA_ENDPOINT_CREATED)
async def admin_endpoint_bq_schema(
    ctx: SimulatorContext,
    admin_client: SyftClient,
    worker_pool: str | None = None,
):
    path = "bigquery.schema"
    schema_function = make_schema(
        settings={
            "calls_per_min": 5,
        },
        worker_pool=worker_pool,
    )

    try:
        ctx.logger.info(f"Admin: Creating endpoint '{path}'")
        result = admin_client.custom_api.add(endpoint=schema_function)
        assert isinstance(result, sy.SyftSuccess), result
    except sy.SyftException as e:
        ctx.logger.error(f"Admin: Failed to add api endpoint '{path}' - {e}")


@sim_activity(trigger=Event.ADMIN_BQ_TEST_ENDPOINT_CREATED)
async def admin_endpoint_bq_test(
    ctx: SimulatorContext,
    admin_client: SyftClient,
    worker_pool: str | None = None,
):
    path = "bigquery.test_query"

    private_query_function = make_test_query(
        settings={
            "rate_limiter_enabled": False,
        }
    )
    mock_query_function = make_test_query(
        settings={
            "rate_limiter_enabled": True,
            "calls_per_min": 10,
        }
    )

    new_endpoint = sy.TwinAPIEndpoint(
        path=path,
        description="This endpoint allows to query Bigquery storage via SQL queries.",
        private_function=private_query_function,
        mock_function=mock_query_function,
        worker_pool=worker_pool,
    )

    try:
        ctx.logger.info(f"Admin: Creating endpoint '{path}'")
        result = admin_client.custom_api.add(endpoint=new_endpoint)
        assert isinstance(result, sy.SyftSuccess), result
    except sy.SyftException as e:
        ctx.logger.error(f"Admin: Failed to add api endpoint '{path}' - {e}")


@sim_activity(trigger=Event.ADMIN_BQ_SUBMIT_ENDPOINT_CREATED)
async def admin_endpoint_bq_submit(
    ctx: SimulatorContext,
    admin_client: sy.DatasiteClient,
    worker_pool: str | None = None,
):
    """Setup on Low Side"""

    path = "bigquery.submit_query"

    @sy.api_endpoint(
        path=path,
        description="API endpoint that allows you to submit SQL queries to run on the private data.",
        worker_pool=worker_pool,
        settings={"worker": worker_pool},
    )
    def submit_query(
        context,
        func_name: str,
        query: str,
    ) -> str:
        # stdlib
        import hashlib

        # syft absolute
        import syft as sy

        hash_object = hashlib.new("sha256")
        hash_object.update(context.user.email.encode("utf-8"))
        func_name = func_name + "_" + hash_object.hexdigest()[:6]

        @sy.syft_function(
            name=func_name,
            input_policy=sy.MixedInputPolicy(
                endpoint=sy.Constant(
                    val=context.admin_client.api.services.bigquery.test_query
                ),
                query=sy.Constant(val=query),
                client=context.admin_client,
            ),
            worker_pool_name=context.settings["worker"],
        )
        def execute_query(query: str, endpoint):
            res = endpoint(sql_query=query)
            return res

        request = context.user_client.code.request_code_execution(execute_query)
        if isinstance(request, sy.SyftError):
            return request
        context.admin_client.requests.set_tags(request, ["autosync"])

        return f"Query submitted {request}. Use `client.code.{func_name}()` to run your query"

    try:
        ctx.logger.info(f"Admin: Creating endpoint '{path}'")
        result = admin_client.custom_api.add(endpoint=submit_query)
        assert isinstance(result, sy.SyftSuccess), result
    except sy.SyftException as e:
        ctx.logger.error(f"Admin: Failed to add api endpoint '{path}' - {e}")


@sim_activity(trigger=Event.ADMIN_ALL_ENDPOINTS_CREATED)
async def admin_create_endpoint(ctx: SimulatorContext, admin_client: SyftClient):
    worker_pool = "biquery-pool"

    await asyncio.gather(
        admin_endpoint_bq_test(ctx, admin_client, worker_pool=worker_pool),
        admin_endpoint_bq_submit(ctx, admin_client, worker_pool=worker_pool),
        admin_endpoint_bq_schema(ctx, admin_client, worker_pool=worker_pool),
    )
    ctx.logger.info("Admin: Created all endpoints")


@sim_activity(
    wait_for=[
        Event.ADMIN_SYNCED_HIGH_TO_LOW,
        # endpoints work only after low side worker pool is created
        Event.ADMIN_LOWSIDE_WORKER_POOL_CREATED,
    ]
)
async def admin_watch_sync(ctx: SimulatorContext, admin_client: SyftClient):
    # fuckall function that just watches for ADMIN_SYNCED_HIGH_TO_LOW
    # only to trigger ADMIN_LOW_SIDE_ENDPOINTS_AVAILABLE that
    ctx.logger.info("Admin: Got a sync from high-side.")

    # trigger any event we want after sync
    ctx.events.trigger(Event.ADMIN_LOW_SIDE_ENDPOINTS_AVAILABLE)


# @sim_activity(trigger=Event.ADMIN_WORKER_POOL_CREATED)
async def admin_create_bq_pool(ctx: SimulatorContext, admin_client: SyftClient):
    worker_pool = "biquery-pool"

    base_image = admin_client.images.get_all()[0]

    external_registry_url = "k3d-registry.localhost:5800"
    worker_image_tag = str(base_image.image_identifier).replace(
        "backend", "worker-bigquery"
    )

    worker_dockerfile = f"""
    FROM {str(base_image.image_identifier)}
    RUN uv pip install db-dtypes google-cloud-bigquery
    """.strip()

    ctx.logger.info(f"Admin: Creating worker pool with tag='{worker_image_tag}'")

    # build_and_launch_worker_pool_from_docker_str is a blocking call
    # so you just run it in a different thread.
    await ctx.blocking_call(
        build_and_launch_worker_pool_from_docker_str,
        environment="remote",
        client=admin_client,
        worker_pool_name=worker_pool,
        worker_dockerfile=worker_dockerfile,
        external_registry=external_registry_url,
        docker_tag=worker_image_tag,
        custom_pool_pod_annotations=None,
        custom_pool_pod_labels=None,
        scale_to=1,
    )
    ctx.logger.info(f"Admin: Worker pool created with tag='{worker_image_tag}'")


@sim_activity(trigger=Event.ADMIN_HIGHSIDE_WORKER_POOL_CREATED)
async def admin_create_bq_pool_high(ctx: SimulatorContext, admin_client: SyftClient):
    await admin_create_bq_pool(ctx, admin_client)


@sim_activity(trigger=Event.ADMIN_LOWSIDE_WORKER_POOL_CREATED)
async def admin_create_bq_pool_low(ctx: SimulatorContext, admin_client: SyftClient):
    await admin_create_bq_pool(ctx, admin_client)


@sim_activity(
    wait_for=[
        Event.USER_CAN_SUBMIT_QUERY,
        Event.ADMIN_SYNCED_LOW_TO_HIGH,
    ]
)
async def admin_triage_requests(ctx: SimulatorContext, admin_client: SyftClient):
    while True:
        await asyncio.sleep(random.uniform(5, 10))

        # check if there are any requests
        for request in admin_client.requests:
            ctx.logger.info(f"Admin: Found request {request.__dict__}")
            pass
            # ! approve or deny.
            #   * If func_name has `invalid_func` in it, deny.
            #   * If not, then approve
            # ! approved ones can exec succesfully or fail
            # ! whatever is the case, after that just sync back to low-side (taken care by admin_sync_to_low_flow)


@sim_activity(trigger=Event.ADMIN_HIGHSIDE_FLOW_COMPLETED)
async def admin_high_side(ctx: SimulatorContext, admin_auth):
    admin_client = sy.login(**admin_auth)
    ctx.logger.info("Admin high-side: logged in")

    await asyncio.gather(
        admin_create_bq_pool_high(ctx, admin_client),
        admin_create_endpoint(ctx, admin_client),
        admin_triage_requests(ctx, admin_client),
    )


@sim_activity(trigger=Event.ADMIN_LOWSIDE_FLOW_COMPLETED)
async def admin_low_side(ctx: SimulatorContext, admin_auth, users):
    admin_client = sy.login(**admin_auth)
    ctx.logger.info("Admin low-side: logged in")

    await asyncio.gather(
        admin_signup_users(ctx, admin_client, users),
        admin_create_bq_pool_low(ctx, admin_client),
        admin_watch_sync(ctx, admin_client),
    )


# ------------------------------------------------------------------------------------------------


@sim_activity(trigger=Event.ADMIN_SYNC_COMPLETED)
async def admin_sync_to_low_flow(
    ctx: SimulatorContext,
    admin_auth_high,
    admin_auth_low,
):
    high_client = sy.login(**admin_auth_high)
    ctx.logger.info("Admin: logged in to high-side")

    low_client = sy.login(**admin_auth_low)
    ctx.logger.info("Admin: logged in to low-side")

    while True:
        await asyncio.sleep(random.uniform(5, 10))

        result = sy.sync(high_client, low_client)
        if isinstance(result, sy.SyftSuccess):
            ctx.logger.info("Admin: Nothing to sync high->low")
            continue

        ctx.logger.info(f"Admin: Syncing high->low {result}")
        result._share_all()
        result._sync_all()

        # trigger an event so that guest users can start querying
        ctx.events.trigger(Event.ADMIN_SYNCED_HIGH_TO_LOW)
        ctx.logger.info("Admin: Synced high->low")


@sim_activity(trigger=Event.ADMIN_SYNC_COMPLETED)
async def admin_sync_to_high_flow(
    ctx: SimulatorContext, admin_auth_high, admin_auth_low
):
    high_client = sy.login(**admin_auth_high)
    ctx.logger.info("Admin: logged in to high-side")

    low_client = sy.login(**admin_auth_low)
    ctx.logger.info("Admin: logged in to low-side")

    while True:
        await asyncio.sleep(random.uniform(5, 10))

        result = sy.sync(low_client, high_client)
        if isinstance(result, sy.SyftSuccess):
            ctx.logger.info("Admin: Nothing to sync low->high")
            continue

        ctx.logger.info(f"Admin: Syncing low->high {result}")
        result._share_all()
        result._sync_all()

        ctx.events.trigger(Event.ADMIN_SYNCED_LOW_TO_HIGH)
        ctx.logger.info("Admin: Synced low->high")


# ------------------------------------------------------------------------------------------------


@sim_entrypoint()
async def sim_l0_scenario(ctx: SimulatorContext):
    users = [
        dict(  # noqa: C408
            name=fake.name(),
            email=fake.email(),
            password="password",
        )
        for i in range(3)
    ]

    server_url_high = "http://localhost:8080"
    admin_auth_high = dict(  # noqa: C408, F841
        url=server_url_high,
        email="info@openmined.org",
        password="changethis",
    )

    server_url_low = "http://localhost:8081"
    admin_auth_low = dict(  # noqa: C408
        url=server_url_low,
        email="info@openmined.org",
        password="changethis",
    )

    ctx.events.trigger(Event.INIT)
    await asyncio.gather(
        admin_low_side(ctx, admin_auth_low, users),
        admin_high_side(ctx, admin_auth_high),
        admin_sync_to_low_flow(ctx, admin_auth_high, admin_auth_low),
        admin_sync_to_high_flow(ctx, admin_auth_high, admin_auth_low),
        *[user_flow(ctx, server_url_low, user) for user in users],
    )


@pytest.mark.asyncio
async def test_l0_scenario(request):
    sim = Simulator()

    await sim.start(
        sim_l0_scenario,
        random_wait=None,  # (0.5, 1.5),
        check_events=[
            # admin lowside
            Event.GUEST_USERS_CREATED,
            Event.ADMIN_LOWSIDE_WORKER_POOL_CREATED,
            Event.ADMIN_LOWSIDE_FLOW_COMPLETED,
            # admin high side
            Event.ADMIN_ALL_ENDPOINTS_CREATED,
            Event.ADMIN_HIGHSIDE_WORKER_POOL_CREATED,
            Event.ADMIN_HIGHSIDE_FLOW_COMPLETED,
            # admin sync
            Event.ADMIN_SYNC_COMPLETED,
            # users
            # Event.USER_CAN_QUERY_TEST_ENDPOINT,
            # Event.USER_FLOW_COMPLETED,
        ],
        timeout=300,
    )
