import dataclasses as dc
import itertools
import typing as t

from overrides import override

import superduperdb as s
from superduperdb.components.component import Component
from superduperdb.base.document import Document
from superduperdb.components.listener import Listener
from superduperdb.base.datalayer import Datalayer
from superduperdb.misc.special_dicts import MongoStyleDict
from superduperdb.vector_search.base import VectorIndexMeasureType

if t.TYPE_CHECKING:
    pass

T = t.TypeVar('T')


def ibatch(iterable: t.Iterable[T], batch_size: int) -> t.Iterator[t.List[T]]:
    """
    Batch an iterable into chunks of size `batch_size`

    :param iterable: the iterable to batch
    :param batch_size: the number of groups to write
    """
    iterator = iter(iterable)
    while True:
        batch = list(itertools.islice(iterator, batch_size))
        if not batch:
            break
        yield batch


@dc.dataclass
class VectorIndex(Component):
    """
    A component carrying the information to apply a vector index to a ``DB`` instance

    :param identifier: Unique string identifier of index
    :param indexing_listener: Listener which is applied to created vectors
    :param compatible_listener: Listener which is applied to vectors to be compared
    :param measure: Measure to use for comparison
    :param version: version of this index
    :param metric_values: Metric values for this index
    """

    identifier: str
    indexing_listener: t.Union[Listener, str]
    compatible_listener: t.Union[None, Listener, str] = None
    measure: VectorIndexMeasureType = VectorIndexMeasureType.cosine
    version: t.Optional[int] = None
    metric_values: t.Optional[t.Dict] = dc.field(default_factory=dict)

    type_id: t.ClassVar[str] = 'vector_index'

    @override
    def on_create(self, db: Datalayer) -> None:
        if s.CFG.vector_search == s.CFG.data_backend:
            if (create := getattr(db.databackend, 'create_vector_index', None)) is None:
                msg = 'VectorIndex is not supported by the current database backend'
                raise ValueError(msg)

            create(self)

    @override
    def on_load(self, db: Datalayer) -> None:
        if isinstance(self.indexing_listener, str):
            self.indexing_listener = t.cast(
                Listener, db.load('listener', self.indexing_listener)
            )

        if isinstance(self.compatible_listener, str):
            self.compatible_listener = t.cast(
                Listener, db.load('listener', self.compatible_listener)
            )

    @property
    def child_components(self) -> t.Sequence[t.Tuple[str, str]]:
        out = [('indexing_listener', 'listener')]
        if self.compatible_listener is not None:
            out.append(('compatible_listener', 'listener'))
        return out

    def get_vector(
        self,
        like: Document,
        models: t.List[str],
        keys: t.List[str],
        db: t.Any = None,
        outputs: t.Optional[t.Dict] = None,
        featurize: bool = True,
    ):
        document = MongoStyleDict(like.unpack())
        if featurize:
            outputs = outputs or {}
            if '_outputs' not in document:
                document['_outputs'] = {}
            document['_outputs'].update(outputs)

            assert not isinstance(self.indexing_listener, str)
            features = self.indexing_listener.features or ()
            for subkey in features:
                subout = document['_outputs'].setdefault(subkey, {})
                f_subkey = features[subkey]
                if f_subkey not in subout:
                    subout[f_subkey] = db.models[f_subkey]._predict(document[subkey])
                document[subkey] = subout[f_subkey]
        available_keys = list(document.keys()) + ['_base']
        try:
            model_name, key = next(
                (m, k) for m, k in zip(models, keys) if k in available_keys
            )
        except StopIteration:
            raise Exception(
                f'Keys in provided {like} don\'t match'
                f'VectorIndex keys: {keys}, with model: {models}'
            )

        model_input = document
        if key == '_base' and key in document:
            model_input = document[key]
        elif key != '_base':
            model_input = document[key]

        model = db.models[model_name]
        return model.predict(model_input, one=True), model.identifier, key

    def get_nearest(
        self,
        like: Document,
        db: t.Any,
        id_field: str = '_id',
        outputs: t.Optional[t.Dict] = None,
        featurize: bool = True,
        ids: t.Optional[t.Sequence[str]] = None,
        n: int = 100,
    ) -> t.Tuple[t.List[str], t.List[float]]:
        """Given a document, find the nearest results in this vector index, returned as
        two parallel lists of result IDs and scores

        :param like: The document to compare against
        :param db: The datastore to use
        :param outputs: An optional dictionary
        :param featurize: Enable featurization
        :param ids: A list of ids to match
        :param n: Number of items to return
        """

        models, keys = self.models_keys
        if len(models) != len(keys):
            raise ValueError(f'len(model={models}) != len(keys={keys})')
        within_ids = ids or ()

        if isinstance(like.content, dict) and id_field in like.content:
            return db.fast_vector_searchers[self.identifier].find_nearest_from_id(
                str(like[id_field]), within_ids=within_ids, limit=n
            )
        h = self.get_vector(
            like=like,
            models=models,
            keys=keys,
            db=db,
            outputs=outputs,
            featurize=featurize,
        )[0]

        return db.fast_vector_searchers[self.identifier].find_nearest_from_array(
            h, within_ids=within_ids, n=n
        )

    @property
    def models_keys(self) -> t.Tuple[t.List[str], t.List[str]]:
        """
        Return a list of model and keys for each listener
        """
        assert not isinstance(self.indexing_listener, str)
        assert not isinstance(self.compatible_listener, str)

        if self.compatible_listener:
            listeners = [self.indexing_listener, self.compatible_listener]
        else:
            listeners = [self.indexing_listener]

        models = [w.model.identifier for w in listeners]  # type: ignore[union-attr]
        keys = [w.key for w in listeners]
        return models, keys

    @property
    def dimensions(self) -> int:
        assert not isinstance(self.indexing_listener, str)
        assert not isinstance(self.indexing_listener.model, str)
        if shape := getattr(self.indexing_listener.model.encoder, 'shape', None):
            return shape[-1]
        raise ValueError('Couldn\'t get shape of model outputs from model encoder')
