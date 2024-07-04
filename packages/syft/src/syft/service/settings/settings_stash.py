# stdlib

# third party

# relative
from ...node.credentials import SyftVerifyKey
from ...serde.serializable import serializable
from ...store.document_store import DocumentStore
from ...store.document_store import NewBaseUIDStoreStash
from ...store.document_store import PartitionKey
from ...store.document_store import PartitionSettings
from ...store.document_store_errors import StashException
from ...types.result import as_result
from ...types.uid import UID
from ...util.telemetry import instrument
from .settings import NodeSettings

NamePartitionKey = PartitionKey(key="name", type_=str)
ActionIDsPartitionKey = PartitionKey(key="action_ids", type_=list[UID])


@instrument
@serializable()
class SettingsStash(NewBaseUIDStoreStash):
    object_type = NodeSettings
    settings: PartitionSettings = PartitionSettings(
        name=NodeSettings.__canonical_name__, object_type=NodeSettings
    )

    def __init__(self, store: DocumentStore) -> None:
        super().__init__(store=store)

    # Should we have this at all?
    @as_result(StashException)
    def update(
        self,
        credentials: SyftVerifyKey,
        settings: NodeSettings,
        has_permission: bool = False,
    ) -> NodeSettings:
        obj = self.check_type(settings, self.object_type).unwrap()
        # Is this working at all?
        return super().update(credentials=credentials, obj=obj).unwrap()
