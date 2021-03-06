import torch
from torch import sparse

import itertools
import random
import unittest
from common import TestCase, run_tests
from common_nn import TEST_CUDA
from test_torch import TestTorch
from numbers import Number


def cpu_only(inner):
    def outer(self, *args, **kwargs):
        if self.is_cuda:
            raise unittest.SkipTest("Test is CPU-only")
        inner(self, *args, **kwargs)
    return outer


def cuda_only(inner):
    def outer(self, *args, **kwargs):
        if not self.is_cuda:
            raise unittest.SkipTest("Test is GPU-only")
        inner(self, *args, **kwargs)
    return outer


class TestSparse(TestCase):

    def setUp(self):
        # These parameters control the various ways we can run the test.
        # We will subclass and override this method to implement CUDA
        # tests
        self.is_cuda = False
        self.is_uncoalesced = False
        self.IndexTensor = torch.LongTensor
        self.ValueTensor = torch.DoubleTensor
        self.SparseTensor = torch.sparse.DoubleTensor
        super(TestSparse, self).setUp()

    def _gen_sparse(self, d, nnz, with_size):
        # TODO: Consider implementing this in the CUDA case by directly
        # performing the operations on the GPU.  You won't be able to
        # use torch.rand/torch.randn in this case because they are
        # CPU-only.  If you do this, you can remove the is_cuda branch
        # at the end.
        #
        # If you do this, be sure to update assert_uncoalesced too

        if isinstance(with_size, Number):
            with_size = [with_size] * d

        if self.is_uncoalesced:
            # We want to generate a tensor with a lot of uncoalesced
            # entries to stress test whether or not we handle this
            # (subtle) case correctly
            v_size = [nnz * 2] + list(with_size[d:])
            v = torch.randn(*v_size)
            r = torch.rand(d, nnz)
            # Repeat the indexes, so every position shows up twice
            i = torch.cat([r, r], dim=1) * \
                torch.Tensor(with_size[:d]).repeat(nnz * 2, 1).transpose(0, 1)
            i = i.type(torch.LongTensor)
            x = torch.sparse.DoubleTensor(i, v, torch.Size(with_size))
            self.assert_uncoalesced(x)
        else:
            # Generate a sparse tensor with d sparse dimensions; the
            # rest the dimensions with_size[d:] are dense.
            v_size = [nnz] + list(with_size[d:])
            v = torch.randn(*v_size)
            i = torch.rand(d, nnz) * \
                torch.Tensor(with_size[:d]).repeat(nnz, 1).transpose(0, 1)
            i = i.type(torch.LongTensor)
            x = torch.sparse.DoubleTensor(i, v, torch.Size(with_size))

        if self.is_cuda:
            return x.cuda(), i.cuda(), v.cuda()
        else:
            return x, i.clone(), v.clone()

    def assert_uncoalesced(self, x):
        """
        Test if a CPU tensor is uncoalesced.  This is used to ensure
        correctness of the uncoalesced tensor generation algorithm.
        """
        assert not x.is_coalesced()
        # Strategy: construct a new sparse tensor with the raw value
        # field overwritten to a tensor of ones, coalesce it, and then
        # check if any value entries are > 1 (which indicates that the
        # original was uncoalesced.)
        i = x._indices().clone()
        v = x._values().clone().fill_(1)
        y = torch.sparse.DoubleTensor(i, v, x.size())
        z = self.safeCoalesce(y)
        assert (z._values() > 1).sum() > 0

    def randn(self, *args, **kwargs):
        """
        Variant of torch.randn that also works in the TEST_CUDA case.
        """
        # TODO: Put this in torch.cuda.randn
        return self.ValueTensor(*args, **kwargs).normal_()

    def test_basic(self):
        x, i, v = self._gen_sparse(3, 10, 100)

        self.assertEqual(i, x._indices())
        self.assertEqual(v, x._values())

        x, i, v = self._gen_sparse(3, 10, [100, 100, 100])
        self.assertEqual(i, x._indices())
        self.assertEqual(v, x._values())
        self.assertEqual(x.ndimension(), 3)
        self.assertEqual(self.safeCoalesce(x)._nnz(), 10)
        for i in range(3):
            self.assertEqual(x.size(i), 100)

        # Make sure that coalesce handles duplicate indices correctly
        i = self.IndexTensor([[9, 0, 0, 0, 8, 1, 1, 1, 2, 7, 2, 2, 3, 4, 6, 9]])
        v = self.ValueTensor([[idx**2, idx] for idx in range(i.size(1))])
        x = self.SparseTensor(i, v, torch.Size([10, 2]))
        self.assertEqual(self.safeCoalesce(x)._nnz(), 9)

        # Make sure we can access empty indices / values
        x = self.SparseTensor()
        self.assertEqual(x._indices().numel(), 0)
        self.assertEqual(x._values().numel(), 0)

    def test_ctor_size_checks(self):
        indices = self.IndexTensor([
            [0, 0, 0],
            [0, 3, 0],
            [0, 0, 0],
            [0, 0, 0],
        ])
        values = self.ValueTensor([2, 1, 3, 4])

        # indices inconsistent with size
        self.assertRaises(
            RuntimeError,
            lambda: self.SparseTensor(indices, values, torch.Size([2, 1, 1])))

        # values inconsistent with size
        values = self.ValueTensor([
            [2, 1, 2, 1],
            [1, 0, 5, 2],
        ])
        self.assertRaises(
            RuntimeError,
            lambda: self.SparseTensor(indices, values, torch.Size([2, 4, 2, 1])))

    def test_to_dense(self):
        i = self.IndexTensor([
            [0, 1, 2, 2],
            [0, 0, 0, 3],
            [0, 0, 1, 4],
        ])
        v = self.ValueTensor([2, 1, 3, 4])
        x = self.SparseTensor(i, v, torch.Size([3, 4, 5]))
        res = self.ValueTensor([
            [[2, 0, 0, 0, 0],
             [0, 0, 0, 0, 0],
             [0, 0, 0, 0, 0],
             [0, 0, 0, 0, 0]],
            [[1, 0, 0, 0, 0],
             [0, 0, 0, 0, 0],
             [0, 0, 0, 0, 0],
             [0, 0, 0, 0, 0]],
            [[0, 3, 0, 0, 0],
             [0, 0, 0, 0, 0],
             [0, 0, 0, 0, 0],
             [0, 0, 0, 0, 4]],
        ])

        x.to_dense()  # Tests double to_dense for memory corruption
        x.to_dense()
        x.to_dense()
        self.assertEqual(res, x.to_dense())
        self.assertEqual(res, self.safeToDense(x))

    def test_shared(self):
        i = self.IndexTensor([[2]])
        v = self.ValueTensor([5])
        x = self.SparseTensor(i, v, torch.Size([3]))
        v[0] = 6
        self.assertEqual(self.ValueTensor([0, 0, 6]), self.safeToDense(x))
        i[0][0] = 0
        self.assertEqual(self.ValueTensor([6, 0, 0]), self.safeToDense(x))

    def test_to_dense_hybrid(self):
        i = self.IndexTensor([
            [0, 1, 2, 2],
            [0, 0, 0, 3],
        ])
        v = self.ValueTensor([[2, 3], [1, 2], [3, 4], [4, 5]])
        x = self.SparseTensor(i, v, torch.Size([3, 4, 2]))
        res = self.ValueTensor([
            [[2, 3],
             [0, 0],
             [0, 0],
             [0, 0]],
            [[1, 2],
             [0, 0],
             [0, 0],
             [0, 0]],
            [[3, 4],
             [0, 0],
             [0, 0],
             [4, 5]],
        ])

        x.to_dense()  # Tests double to_dense for memory corruption
        x.to_dense()
        x.to_dense()
        self.assertEqual(res, x.to_dense())
        self.assertEqual(res, self.safeToDense(x))

    def test_contig(self):
        i = self.IndexTensor([
            [1, 0, 35, 14, 39, 6, 71, 66, 40, 27],
            [92, 31, 62, 50, 22, 65, 89, 74, 56, 34],
        ])
        v = self.ValueTensor([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        x = self.SparseTensor(i, v, torch.Size([100, 100]))
        exp_i = self.IndexTensor([
            [0, 1, 6, 14, 27, 35, 39, 40, 66, 71],
            [31, 92, 65, 50, 34, 62, 22, 56, 74, 89],
        ])
        exp_v = self.ValueTensor([2, 1, 6, 4, 10, 3, 5, 9, 8, 7])
        x = self.safeCoalesce(x)
        self.assertEqual(exp_i, x._indices())
        self.assertEqual(exp_v, x._values())

        i = self.IndexTensor([
            [2, 0, 2, 1],
            [0, 0, 3, 0],
            [1, 0, 4, 0],
        ])
        v = self.ValueTensor([3, 2, 4, 1])
        x = self.SparseTensor(i, v, torch.Size([3, 4, 5]))
        exp_i = self.IndexTensor([
            [0, 1, 2, 2],
            [0, 0, 0, 3],
            [0, 0, 1, 4],
        ])
        exp_v = self.ValueTensor([2, 1, 3, 4])

        x = self.safeCoalesce(x)
        self.assertEqual(exp_i, x._indices())
        self.assertEqual(exp_v, x._values())

        # Duplicate indices
        i = self.IndexTensor([
            [0, 0, 2, 0],
            [0, 0, 3, 0],
            [0, 0, 4, 0],
        ])
        v = self.ValueTensor([3, 2, 4, 1])
        x = self.SparseTensor(i, v, torch.Size([3, 4, 5]))
        exp_i = self.IndexTensor([
            [0, 2],
            [0, 3],
            [0, 4],
        ])
        exp_v = self.ValueTensor([6, 4])

        x = self.safeCoalesce(x)
        self.assertEqual(exp_i, x._indices())
        self.assertEqual(exp_v, x._values())

    def test_contig_hybrid(self):
        i = self.IndexTensor([
            [1, 0, 35, 14, 39, 6, 71, 66, 40, 27],
            [92, 31, 62, 50, 22, 65, 89, 74, 56, 34],
        ])
        v = self.ValueTensor([
            [1, 2], [2, 3], [3, 4], [4, 5], [5, 6],
            [6, 7], [7, 8], [8, 9], [9, 10], [10, 11],
        ])
        x = self.SparseTensor(i, v, torch.Size([100, 100, 2]))
        exp_i = self.IndexTensor([
            [0, 1, 6, 14, 27, 35, 39, 40, 66, 71],
            [31, 92, 65, 50, 34, 62, 22, 56, 74, 89],
        ])
        exp_v = self.ValueTensor([
            [2, 3], [1, 2], [6, 7], [4, 5], [10, 11],
            [3, 4], [5, 6], [9, 10], [8, 9], [7, 8],
        ])
        x = self.safeCoalesce(x)
        self.assertEqual(exp_i, x._indices())
        self.assertEqual(exp_v, x._values())

        i = self.IndexTensor([
            [2, 0, 2, 1],
            [0, 0, 3, 0],
            [1, 0, 4, 0],
        ])
        v = self.ValueTensor([[3, 3, 3], [2, 2, 2], [4, 4, 4], [1, 1, 1]])
        x = self.SparseTensor(i, v, torch.Size([3, 4, 5, 3]))
        exp_i = self.IndexTensor([
            [0, 1, 2, 2],
            [0, 0, 0, 3],
            [0, 0, 1, 4],
        ])
        exp_v = self.ValueTensor([[2, 2, 2], [1, 1, 1], [3, 3, 3], [4, 4, 4]])

        x = self.safeCoalesce(x)
        self.assertEqual(exp_i, x._indices())
        self.assertEqual(exp_v, x._values())

        # Duplicate indices
        i = self.IndexTensor([
            [0, 0, 2, 0],
            [0, 0, 3, 0],
            [0, 0, 4, 0],
        ])
        v = self.ValueTensor([[3, 2, 3], [2, 1, 1], [4, 3, 4], [1, 1, 1]])
        x = self.SparseTensor(i, v, torch.Size([3, 4, 5, 3]))
        exp_i = self.IndexTensor([
            [0, 2],
            [0, 3],
            [0, 4],
        ])
        exp_v = self.ValueTensor([[6, 4, 5], [4, 3, 4]])

        x = self.safeCoalesce(x)
        self.assertEqual(exp_i, x._indices())
        self.assertEqual(exp_v, x._values())

    def test_clone(self):
        x, _, _ = self._gen_sparse(4, 20, 5)
        if self.is_uncoalesced:
            self.assertFalse(x.is_coalesced())
            y = x.clone()
            self.assertFalse(y.is_coalesced())
        x = x.coalesce()
        self.assertTrue(x.is_coalesced())
        y = x.clone()
        self.assertTrue(y.is_coalesced())

    @cuda_only
    def test_cuda_empty(self):
        x = torch.sparse.FloatTensor(2, 3, 4)
        y = x.cuda(0)
        self.assertEqual(x._dimI(), y._dimI())
        self.assertEqual(x._dimV(), y._dimV())
        x = y.cpu()
        self.assertEqual(y._dimI(), x._dimI())
        self.assertEqual(y._dimV(), x._dimV())

    def test_transpose(self):
        x = self._gen_sparse(4, 20, 5)[0]
        y = self.safeToDense(x)

        for i, j in itertools.combinations(range(4), 2):
            x = x.transpose_(i, j)
            y = y.transpose(i, j)
            self.assertEqual(self.safeToDense(x), y)

            x = x.transpose(i, j)
            y = y.transpose(i, j)
            self.assertEqual(self.safeToDense(x), y)

    def test_transpose_coalesce_invariant(self):
        # If a sparse tensor is coalesced, its transpose should be the same
        # If a sparse tensor is uncoalesed, its transpose should be the same
        x_coalesced = self._gen_sparse(2, 3, 4)[0].coalesce()
        x_indices = x_coalesced._indices()
        x_values = x_coalesced._values()

        y_uncoalesced = self.SparseTensor(
            torch.cat([x_indices, x_indices], dim=1),
            torch.cat([x_values, x_values]),
            x_coalesced.size())

        self.assertTrue(x_coalesced.is_coalesced())
        self.assertFalse(y_uncoalesced.is_coalesced())

        self.assertTrue(x_coalesced.transpose(0, 1).is_coalesced())
        self.assertFalse(y_uncoalesced.transpose(0, 1).is_coalesced())

        x_coalesced.transpose_(0, 1)
        y_uncoalesced.transpose_(0, 1)
        self.assertTrue(x_coalesced.is_coalesced())
        self.assertFalse(y_uncoalesced.is_coalesced())

    @cpu_only
    def test_mm(self):
        def test_shape(di, dj, dk):
            x, _, _ = self._gen_sparse(2, 20, [di, dj])
            t = torch.randn(di, dk)
            y = torch.randn(dj, dk)
            alpha = random.random()
            beta = random.random()

            res = torch.addmm(alpha, t, beta, x, y)
            expected = torch.addmm(alpha, t, beta, self.safeToDense(x), y)
            self.assertEqual(res, expected)

            res = torch.addmm(t, x, y)
            expected = torch.addmm(t, self.safeToDense(x), y)
            self.assertEqual(res, expected)

            res = torch.mm(x, y)
            expected = torch.mm(self.safeToDense(x), y)
            self.assertEqual(res, expected)

        test_shape(10, 100, 100)
        test_shape(100, 1000, 200)
        test_shape(64, 10000, 300)

    @cpu_only
    def test_saddmm(self):
        def test_shape(di, dj, dk):
            x = self._gen_sparse(2, 20, [di, dj])[0]
            t = self._gen_sparse(2, 20, [di, dk])[0]
            y = torch.randn(dj, dk)
            alpha = random.random()
            beta = random.random()

            res = torch.saddmm(alpha, t, beta, x, y)
            expected = torch.addmm(alpha, self.safeToDense(t), beta, self.safeToDense(x), y)
            self.assertEqual(self.safeToDense(res), expected)

            res = torch.saddmm(t, x, y)
            expected = torch.addmm(self.safeToDense(t), self.safeToDense(x), y)
            self.assertEqual(self.safeToDense(res), expected)

            res = torch.smm(x, y)
            expected = torch.mm(self.safeToDense(x), y)
            self.assertEqual(self.safeToDense(res), expected)

        test_shape(7, 5, 3)
        test_shape(1000, 100, 100)
        test_shape(3000, 64, 300)

    def test_dsmm(self):
        def test_shape(di, dj, dk):
            x = self._gen_sparse(2, 20, [di, dj])[0]
            y = self.randn(dj, dk)

            res = torch.dsmm(x, y)
            expected = torch.mm(self.safeToDense(x), y)
            self.assertEqual(res, expected)

        test_shape(7, 5, 3)
        test_shape(1000, 100, 100)
        test_shape(3000, 64, 300)

    def test_hsmm(self):
        def test_shape(di, dj, dk):
            x = self._gen_sparse(2, 20, [di, dj])[0]
            y = self.randn(dj, dk)

            res = torch.hsmm(x, y)
            # TODO: use self.safeToDense(), but this triggers
            # https://github.com/pytorch/pytorch/issues/3170
            expected = torch.mm(x.to_dense(), y)
            self.assertEqual(res.to_dense(), expected)

        test_shape(7, 5, 3)
        test_shape(1000, 100, 100)
        test_shape(3000, 64, 300)

    def _test_spadd_shape(self, shape_i, shape_v=None):
        shape = shape_i + (shape_v or [])
        x, _, _ = self._gen_sparse(len(shape_i), 10, shape)
        y = self.randn(*shape)
        r = random.random()

        res = torch.add(y, r, x)
        expected = y + r * self.safeToDense(x)

        self.assertEqual(res, expected)

        # Non contiguous dense tensor
        s = list(shape)
        s[0] = shape[-1]
        s[-1] = shape[0]
        y = self.randn(*s)
        y.transpose_(0, len(s) - 1)
        r = random.random()

        res = torch.add(y, r, x)
        expected = y + r * self.safeToDense(x)

        self.assertEqual(res, expected)

    def test_spadd(self):
        self._test_spadd_shape([5, 6])
        self._test_spadd_shape([10, 10, 10])
        self._test_spadd_shape([50, 30, 20])
        self._test_spadd_shape([5, 5, 5, 5, 5, 5])

    def test_spadd_hybrid(self):
        self._test_spadd_shape([5, 6], [2, 3])
        self._test_spadd_shape([10, 10, 10], [3])
        self._test_spadd_shape([50, 30, 20], [2])
        self._test_spadd_shape([5, 5, 5, 5, 5, 5], [2])

    def test_norm(self):
        x, _, _ = self._gen_sparse(3, 10, 100)
        y = x.coalesce()
        self.assertEqual(x.norm(), y._values().norm())

    def _test_basic_ops_shape(self, shape_i, shape_v=None):
        shape = shape_i + (shape_v or [])
        x1, _, _ = self._gen_sparse(len(shape_i), 9, shape)
        x2, _, _ = self._gen_sparse(len(shape_i), 12, shape)

        y1 = x1 + x2
        y2 = x1.clone()
        y2.add_(x2)
        expected = self.safeToDense(x1) + self.safeToDense(x2)
        self.assertEqual(self.safeToDense(y1), expected)
        self.assertEqual(self.safeToDense(y2), expected)

        y1 = x1 - x2
        y2 = x1.clone()
        y2.sub_(x2)
        expected = self.safeToDense(x1) - self.safeToDense(x2)
        self.assertEqual(self.safeToDense(y1), expected)
        self.assertEqual(self.safeToDense(y2), expected)

        y1 = x1 * x2
        y2 = x1.clone()
        y2.mul_(x2)
        expected = self.safeToDense(x1) * self.safeToDense(x2)
        self.assertEqual(self.safeToDense(y1), expected)
        self.assertEqual(self.safeToDense(y2), expected)

        y1 = x1 * 37.5
        y2 = x1.clone()
        y2.mul_(37.5)
        expected = self.safeToDense(x1) * 37.5
        self.assertEqual(self.safeToDense(y1), expected)
        self.assertEqual(self.safeToDense(y2), expected)

        y1 = x1 / 37.5
        y2 = x1.clone()
        y2.div_(37.5)
        expected = self.safeToDense(x1) / 37.5
        self.assertEqual(self.safeToDense(y1), expected)
        self.assertEqual(self.safeToDense(y2), expected)

        # TODO: add back inplace support
        y1 = x1 ** 2
        y2 = x1.clone()
        y2 = y2.pow(2)
        expected = self.safeToDense(x1) ** 2
        self.assertEqual(self.safeToDense(y1), expected)
        self.assertEqual(self.safeToDense(y2), expected)

        y = x1.clone()
        y.zero_()
        expected = torch.zeros(x1.size())
        self.assertEqual(self.safeToDense(y), expected)

        self.assertFalse(x1.is_coalesced())
        y = x1.coalesce()
        z = x1.coalesce()
        self.assertFalse(x1.is_coalesced())
        self.assertTrue(y.is_coalesced())
        self.assertEqual(x1, y)
        # check that coalesce is out of place
        y._values().add_(1)
        self.assertEqual(z._values() + 1, y._values())

    def test_basic_ops(self):
        self._test_basic_ops_shape([5, 6])
        self._test_basic_ops_shape([10, 10, 10])
        self._test_basic_ops_shape([50, 30, 20])
        self._test_basic_ops_shape([5, 5, 5, 5, 5, 5])

    def test_basic_ops_hybrid(self):
        self._test_basic_ops_shape([5, 6], [2, 3])
        self._test_basic_ops_shape([10, 10, 10], [3])
        self._test_basic_ops_shape([50, 30, 20], [2])
        self._test_basic_ops_shape([5, 5, 5, 5, 5, 5], [2])

    def _test_sparse_mask_shape(self, shape_i, shape_v=None):
        shape = shape_i + (shape_v or [])
        x1, _, _ = self._gen_sparse(len(shape_i), 9, shape)
        x2, _, _ = self._gen_sparse(len(shape_i), 12, shape)

        y1 = x1 + x2
        y2 = x1.clone()
        y2.add_(x2)
        expected = self.safeToDense(x1) + self.safeToDense(x2)
        self.assertEqual(self.safeToDense(y1), expected)
        self.assertEqual(self.safeToDense(y2), expected)

    def _test_sparse_mask_fixed(self):
        i = self.IndexTensor([
            [1, 3, 0, 4],
            [2, 1, 2, 3],
        ])
        v = self.ValueTensor([1, 2, 3, 4])
        x = self.SparseTensor(i, v, torch.Size([5, 4])).coalesce()
        dense = self.ValueTensor([
            [1, 2, 3, 4],
            [5, 6, 7, 8],
            [9, 10, 11, 12],
            [13, 14, 15, 16],
            [17, 18, 19, 20],
        ])
        exp_v = self.ValueTensor([7, 14, 3, 20])
        res = dense._sparse_mask(x)
        expected = self.SparseTensor(i, exp_v, torch.Size([5, 4]))
        self.assertEqual(res, expected)

    def test_sparse_mask(self):
        self._test_sparse_mask_fixed()

        self._test_sparse_mask_shape([5, 6])
        self._test_sparse_mask_shape([10, 10, 10])
        self._test_sparse_mask_shape([50, 30, 20])
        self._test_sparse_mask_shape([5, 5, 5, 5, 5, 5])

    def _test_zeros(self, shape, out_shape_i, out_shape_v=None):
        out_shape = out_shape_i + (out_shape_v or [])
        for nnz in [9, 12]:
            out, _, _ = self._gen_sparse(len(out_shape_i), nnz, out_shape)
            torch.zeros(*shape, out=out)
            self.assertEqual(tuple(out.size()), tuple(shape))
            self.assertTrue(out._indices().numel() == out._values().numel() == 0)
            self.assertEqual(out._nnz(), 0)
            self.assertEqual(out._dimI(), len(shape))
            self.assertEqual(out._dimV(), 0)

    def test_zeros(self):
        i_shapes = [2, 3, 4]
        v_shapes = [3, 4, 5, 6]
        for i_dim in range(1, len(i_shapes) + 1):
            for v_dim in range(len(v_shapes) + 1):
                self._test_zeros([2, 3, 4], i_shapes[:i_dim], v_shapes[:v_dim])

    def _test_zeros_like(self, template_shape_i, template_shape_v=None):
        template_shape_v = template_shape_v or []
        template_shape = template_shape_i + template_shape_v
        for nnz in [9, 12]:
            t, _, _ = self._gen_sparse(len(template_shape_i), nnz, template_shape)
            res = torch.zeros_like(t)
            self.assertEqual(tuple(res.size()), tuple(template_shape))
            self.assertTrue(res._indices().numel() == res._values().numel() == 0)
            self.assertEqual(res._nnz(), 0)
            self.assertEqual(res._dimI(), len(template_shape_i))
            self.assertEqual(res._dimV(), len(template_shape_v))

    def test_zeros_like(self):
        i_shapes = [2, 3, 4]
        v_shapes = [3, 4, 5, 6]
        for i_dim in range(1, len(i_shapes) + 1):
            for v_dim in range(len(v_shapes) + 1):
                self._test_zeros_like(i_shapes[:i_dim], v_shapes[:v_dim])

    def _test_sparse_mask_hybrid_fixed(self):
        i = self.IndexTensor([
            [1, 3, 0, 4],
            [2, 1, 2, 3],
        ])
        v = self.ValueTensor([[1, 2], [2, 3], [3, 4], [4, 5]])
        # TODO: This is also testing that, if coalesce is a no-op,
        # the indices don't get permuted. I don't know if we actually
        # want to give this invariant.
        x = self.SparseTensor(i, v, torch.Size([5, 4, 2])).coalesce()
        dense = self.ValueTensor([
            [[1, 3], [2, 2], [3, 3], [4, 2]],
            [[5, 7], [6, 7], [7, 9], [8, 9]],
            [[9, 2], [10, 4], [11, 1], [12, 3]],
            [[13, 5], [14, 1], [15, 1], [16, 6]],
            [[17, 7], [18, 2], [19, 7], [20, 1]],
        ])
        res = dense._sparse_mask(x)
        exp_v = self.ValueTensor([[7, 9], [14, 1], [3, 3], [20, 1]])
        expected = self.SparseTensor(i, exp_v, torch.Size([5, 4, 2]))
        self.assertEqual(res, expected)

    def test_sparse_variable_methods(self):
        # TODO: delete when tensor/variable are merged
        from torch.autograd import Variable
        i = self.IndexTensor([[0, 1, 1], [2, 0, 2]])
        v = self.ValueTensor([3, 4, 5])
        sparse_mat = self.SparseTensor(i, v, torch.Size([2, 3]))
        sparse_var = Variable(sparse_mat)

        to_test_one_arg = {
            'zeros_like': lambda x: torch.zeros_like(x),
            'transpose': lambda x: x.transpose(0, 1),
            'transpose_': lambda x: x.transpose(0, 1),
            't': lambda x: x.t(),
            't_': lambda x: x.t_(),
            'div': lambda x: x.div(2),
            'div_': lambda x: x.div_(2),
            'pow': lambda x: x.pow(2),
            '_nnz': lambda x: x._nnz(),
            'is_coalesced': lambda x: x.is_coalesced(),
            'coalesce': lambda x: x.coalesce(),
            'to_dense': lambda x: x.to_dense(),
            '_dimI': lambda x: x._dimI(),
            '_dimV': lambda x: x._dimV(),
            'norm': lambda x: x.norm(),
        }

        for test_name, test_fn in to_test_one_arg.items():
            var1 = sparse_var.clone()
            tensor1 = sparse_mat.clone()

            out_var = test_fn(var1)
            out_tensor = test_fn(tensor1)

            if isinstance(out_tensor, int) or isinstance(out_tensor, bool):
                if not isinstance(out_var, int) and not isinstance(out_var, bool):
                    check_var = out_var.data[0]
                else:
                    check_var = out_var
                self.assertEqual(out_var, out_tensor)
                continue

            # Assume output is variable / tensor
            self.assertEqual(test_fn(var1).data, test_fn(tensor1),
                             test_name)

        i = self.IndexTensor([[0, 0, 1], [1, 2, 1]])
        v = self.ValueTensor([3, 3, 4])
        sparse_mat2 = self.SparseTensor(i, v, torch.Size([2, 3]))
        sparse_var2 = Variable(sparse_mat2)

        to_test_two_arg = {
            'sub': lambda x, y: x.sub(y),
            'sub_': lambda x, y: x.sub_(y),
            'mul': lambda x, y: x.mul(y),
            'mul_': lambda x, y: x.mul_(y),
        }

        for test_name, test_fn in to_test_two_arg.items():
            var1 = sparse_var.clone()
            var2 = sparse_var2.clone()
            tensor1 = sparse_mat.clone()
            tensor2 = sparse_mat2.clone()
            self.assertEqual(test_fn(var1, var2).data,
                             test_fn(tensor1, tensor2), test_name)

        to_test_mixed = [
            # test name, lambda expression, should_run_when_cuda
            ('sspaddmm', lambda sp, de: sp.sspaddmm(sp, de), False),
            ('sspaddmm_b', lambda sp, de: sp.sspaddmm(2, sp, de), False),
            ('sspaddmm_b_a', lambda sp, de: sp.sspaddmm(3, 2, sp, de), False),
            ('addmm', lambda sp, de: de.addmm(sp, de), True),
            ('addmm_', lambda sp, de: de.addmm(sp, de), True),
            ('mm', lambda sp, de: torch.mm(sp, de), True),
            ('mm_out', lambda sp, de: torch.mm(sp, de, out=de), True),
        ]

        i = self.IndexTensor([[0, 0, 1, 2, 2], [1, 2, 1, 0, 1]])
        v = self.ValueTensor([3, 3, 4, 1, 2])
        sparse_mat = self.SparseTensor(i, v, torch.Size([3, 3]))
        sparse_var = Variable(sparse_mat)
        dense_mat = sparse_mat.to_dense().random_(0, 5)
        dense_var = Variable(dense_mat)

        for test_name, test_fn, test_cuda in to_test_mixed:
            if sparse_var.is_cuda and not test_cuda:
                continue
            sp_var = sparse_var.clone()
            de_var = dense_var.clone()
            sp_mat = sparse_mat.clone()
            de_mat = dense_mat.clone()
            self.assertEqual(test_fn(sp_var, de_var).data,
                             test_fn(sp_mat, de_mat), test_name)

    def test_sparse_mask_hybrid(self):
        self._test_sparse_mask_hybrid_fixed()

        self._test_sparse_mask_shape([5, 6], [2, 3])
        self._test_sparse_mask_shape([10, 10, 10], [3])
        self._test_sparse_mask_shape([50, 30, 20], [2])
        self._test_sparse_mask_shape([5, 5, 5, 5, 5, 5], [2])

    def test_sparse_add_coalesce(self):
        i = self.IndexTensor([[1, 2, 1]])
        v = self.ValueTensor([3, 4, 5])
        x = self.SparseTensor(i, v, torch.Size([3]))
        y = self.SparseTensor(i, v, torch.Size([3]))
        z = x + y

        self.assertFalse(z._indices().numel() != 2 and z.is_coalesced())

    @cuda_only
    def test_storage_not_null(self):
        x = torch.cuda.sparse.FloatTensor(2)
        self.assertNotEqual(x.get_device(), -1)

    @cuda_only
    @unittest.skipIf(torch.cuda.device_count() < 2, "only one GPU detected")
    def test_same_gpu(self):
        i = self.IndexTensor([[2]]).cuda(1)
        v = self.ValueTensor([5]).cuda(1)
        x = self.SparseTensor(i, v, torch.Size([3]), device=1)
        self.assertEqual(x.get_device(), 1)
        self.assertEqual(x._values().get_device(), 1)
        self.assertEqual(x._indices().get_device(), 1)

        x = self.SparseTensor(3, device=1)
        self.assertEqual(x.get_device(), 1)
        self.assertEqual(x._values().get_device(), 1)
        self.assertEqual(x._indices().get_device(), 1)

        v = self.ValueTensor([5]).cuda(0)
        self.assertRaises(RuntimeError, lambda: self.SparseTensor(i, v, torch.Size([3])))

    def _test_new_device(self, size, device):
        with torch.cuda.device(device):
            x = torch.cuda.sparse.DoubleTensor(*size)
        self.assertEqual(x.get_device(), device)
        x1 = x.new()
        x2 = x.new(2, 3)
        self.assertEqual(x1.get_device(), device)
        self.assertEqual(x2.get_device(), device)

    @cuda_only
    def test_new_device_single_gpu(self):
        self._test_new_device((), 0)
        self._test_new_device((30, 20), 0)
        self._test_new_device((30, 20, 10), 0)

    @cuda_only
    @unittest.skipIf(torch.cuda.device_count() < 2, "only one GPU detected")
    def test_new_device_multi_gpu(self):
        self._test_new_device((), 1)
        self._test_new_device((30, 20), 1)
        self._test_new_device((30, 20, 10), 1)

    def test_new(self):
        x, indices, values = self._gen_sparse(3, 10, 100)
        if not x.is_cuda:
            # CUDA sparse tensors currently requires the size to be
            # specified if nDimV > 0
            self.assertEqual(x.new(indices, values), x)
        self.assertEqual(x.new(indices, values, x.size()), x)

        self.assertIs(torch.sparse.uint8, x.new(dtype=torch.sparse.uint8).dtype)
        self.assertIs(torch.sparse.uint8, x.new(1, 2, dtype=torch.sparse.uint8).dtype)

    @cpu_only  # not really, but we only really want to run this once
    def test_factory(self):
        default_size = torch.Size([1, 3])
        size = torch.Size([3, 3])
        for include_size in [True, False]:
            for use_tensor_idx in [True, False]:
                for use_tensor_val in [True, False]:
                    for use_cuda in ([False] if not torch.cuda.is_available() else [True, False]):
                        # have to include size with cuda sparse tensors
                        include_size = include_size or use_cuda
                        dtype = torch.cuda.float64 if use_cuda else torch.float64
                        long_dtype = torch.cuda.int64 if use_cuda else torch.int64
                        sparse_dtype = torch.cuda.sparse.float64 if use_cuda else torch.sparse.float64
                        device = -1 if not use_cuda else torch.cuda.device_count() - 1
                        indices = torch.tensor(([0], [2]), dtype=long_dtype) if use_tensor_idx else ([0], [2])
                        values = torch.tensor([1.], dtype=dtype) if use_tensor_val else 1.
                        if include_size:
                            sparse_tensor = torch.sparse_coo_tensor(indices, values, size, dtype=sparse_dtype,
                                                                    device=device, requires_grad=True)
                        else:
                            sparse_tensor = torch.sparse_coo_tensor(indices, values, dtype=sparse_dtype,
                                                                    device=device, requires_grad=True)
                        self.assertEqual(indices, sparse_tensor._indices())
                        self.assertEqual(values, sparse_tensor._values())
                        self.assertEqual(size if include_size else default_size, sparse_tensor.size())
                        self.assertEqual(sparse_dtype, sparse_tensor.dtype)
                        if use_cuda:
                            self.assertEqual(device, sparse_tensor._values().get_device())
                        self.assertEqual(True, sparse_tensor.requires_grad)

    @cpu_only
    def test_factory_type_inference(self):
        t = torch.sparse_coo_tensor(torch.tensor(([0], [2])), torch.tensor([1.], dtype=torch.float32))
        self.assertEqual(torch.sparse.float32, t.dtype)
        t = torch.sparse_coo_tensor(torch.tensor(([0], [2])), torch.tensor([1.], dtype=torch.float64))
        self.assertEqual(torch.sparse.float64, t.dtype)
        t = torch.sparse_coo_tensor(torch.tensor(([0], [2])), torch.tensor([1]))
        self.assertEqual(torch.sparse.int64, t.dtype)

    @cpu_only
    def test_factory_copy(self):
        # both correct
        indices = torch.tensor(([0], [2]), dtype=torch.int64)
        values = torch.tensor([1.], dtype=torch.float64)
        sparse_tensor = torch.sparse_coo_tensor(indices, values, dtype=torch.sparse.float64)
        self.assertEqual(indices.data_ptr(), sparse_tensor._indices().data_ptr())
        self.assertEqual(values.data_ptr(), sparse_tensor._values().data_ptr())

        # only indices correct
        indices = torch.tensor(([0], [2]), dtype=torch.int64)
        values = torch.tensor([1.], dtype=torch.float32)
        sparse_tensor = torch.sparse_coo_tensor(indices, values, dtype=torch.sparse.float64)
        self.assertEqual(indices.data_ptr(), sparse_tensor._indices().data_ptr())
        self.assertNotEqual(values.data_ptr(), sparse_tensor._values().data_ptr())

        # only values correct
        indices = torch.tensor(([0], [2]), dtype=torch.int32)
        values = torch.tensor([1.], dtype=torch.float64)
        sparse_tensor = torch.sparse_coo_tensor(indices, values, dtype=torch.sparse.float64)
        self.assertNotEqual(indices.data_ptr(), sparse_tensor._indices().data_ptr())
        self.assertEqual(values.data_ptr(), sparse_tensor._values().data_ptr())

        # neither correct
        indices = torch.tensor(([0], [2]), dtype=torch.int32)
        values = torch.tensor([1.], dtype=torch.float32)
        sparse_tensor = torch.sparse_coo_tensor(indices, values, dtype=torch.sparse.float64)
        self.assertNotEqual(indices.data_ptr(), sparse_tensor._indices().data_ptr())
        self.assertNotEqual(values.data_ptr(), sparse_tensor._values().data_ptr())

    @cpu_only
    def test_factory_non_sparse_dtype(self):
        indices = torch.tensor(([0], [2]), dtype=torch.int64)
        values = torch.tensor([1.])
        size = torch.Size([3, 3])
        self.assertRaises(TypeError, lambda: torch.sparse_coo_tensor(indices, values, dtype=torch.float64))
        self.assertRaises(TypeError, lambda: torch.sparse_coo_tensor(indices, values, size, dtype=torch.float64))
        if torch.cuda.is_available():
            self.assertRaises(TypeError, lambda: torch.sparse_coo_tensor(indices, values, dtype=torch.cuda.float64))
            self.assertRaises(TypeError, lambda: torch.sparse_coo_tensor(indices, values, size, dtype=torch.float64))

    @cpu_only  # not really, but we only really want to run this once
    def test_dtypes(self):
        all_dtypes = torch.testing.get_all_dtypes()
        cpu_dtypes = [d for d in all_dtypes if d.is_sparse and not d.is_cuda]
        cuda_dtypes = [d for d in all_dtypes if d.is_sparse and d.is_cuda]
        TestTorch._test_dtypes(self, cpu_dtypes, cuda_dtypes, True)

    @cpu_only  # not really, but we only really want to run this once
    def test_empty_full(self):
        all_dtypes = torch.testing.get_all_dtypes()
        cpu_dtypes = [d for d in all_dtypes if d.is_sparse and not d.is_cuda]
        cuda_dtypes = [d for d in all_dtypes if d.is_sparse and d.is_cuda]
        TestTorch._test_empty_full(self, cpu_dtypes, cuda_dtypes)

    def test_is_sparse(self):
        x = torch.randn(3, 3)
        self.assertFalse(x.is_sparse)

        x = self.SparseTensor()
        self.assertTrue(x.is_sparse)

    def test_resize_as(self):
        def do_test(t):
            y = t.new().resize_as_(t).zero_()
            self.assertEqual(y.shape, t.shape)
            # Check that y can be added to t. Currently, this requires that
            # _dimI and _dimV match.
            self.assertEqual(t, t + y)

        do_test(self.SparseTensor())


class TestUncoalescedSparse(TestSparse):
    def setUp(self):
        super(TestUncoalescedSparse, self).setUp()
        self.is_uncoalesced = True


@unittest.skipIf(not TEST_CUDA, 'CUDA not available')
class TestCudaSparse(TestSparse):
    def setUp(self):
        super(TestCudaSparse, self).setUp()
        self.is_cuda = True
        self.IndexTensor = torch.cuda.LongTensor
        self.ValueTensor = torch.cuda.DoubleTensor
        self.SparseTensor = torch.cuda.sparse.DoubleTensor


@unittest.skipIf(not TEST_CUDA, 'CUDA not available')
class TestCudaUncoalescedSparse(TestCudaSparse):
    def setUp(self):
        super(TestCudaUncoalescedSparse, self).setUp()
        self.is_uncoalesced = True

if __name__ == '__main__':
    run_tests()
