import numpy

from superduperdb.vector_search.base import BaseVectorIndex
from superduperdb.misc.logger import logging
from superduperdb.vector_search import table_scan


class VanillaVectorIndex(BaseVectorIndex):
    """
    Simple hash-set for looking up with vector similarity.

    :param h: ``torch.Tensor``
    :param index: list of IDs
    :param measure: measure to assess similarity
    """

    name = 'vanilla'

    def __init__(self, h, index, measure='css'):
        if isinstance(measure, str):
            measure = getattr(table_scan, measure)
        super().__init__(h, index, measure)

    def find_nearest_from_hashes(self, h, n=100):
        similarities = self.measure(h, self.h)
        logging.debug(similarities)
        scores = -numpy.sort(-similarities, axis=1)[:, :n]
        ix = numpy.argsort(-similarities, axis=1)[:, :n]
        ix = ix.tolist()
        scores = scores.tolist()
        _ids = [[self.index[i] for i in sub] for sub in ix]
        return _ids, scores

    def __getitem__(self, item):
        ix = [self.lookup[i] for i in item]
        return VanillaVectorIndex(self.h[ix], item, self.measure)


def l2(x, y):
    return numpy.array([-numpy.linalg.norm(x - y, axis=1)])


def dot(x, y):
    return numpy.dot(x, y.T)


def css(x, y):
    x = x / numpy.linalg.norm(x, axis=1)[:, None]
    y = y / numpy.linalg.norm(y, axis=1)[:, None]
    return dot(x, y)