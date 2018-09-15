from __future__ import print_function
from __future__ import division
from __future__ import absolute_import

from experiments.methods.TVGL import TVGL
from scipy.io import savemat, loadmat
from subprocess import Popen, PIPE
from experiments import utils

import sklearn.decomposition as sk_dec
import sklearn.covariance as sk_cov
import linearcorex
import numpy as np
import time
import itertools
import random
import re
import os


class Baseline(object):
    def __init__(self, name):
        self.name = name
        self._trained = False
        self._val_score = None
        self._params = None
        self._covs = None
        self._method = None

    def select(self, train_data, val_data, params, verbose=True):
        if verbose:
            print("\n{}\nSelecting the best parameter values for {} ...".format('-'*80, self.name))

        best_score = 1e18
        best_params = None
        best_covs = None
        best_method = None

        const_params = dict()

        search_params = []
        for k, v in params.items():
            if isinstance(v, list):
                arr = [(k, x) for x in v]
                search_params.append(arr)
            elif isinstance(v, dict):
                arr = []
                for param_k, param_v in v.items():
                    arr += list([(param_k, x) for x in param_v])
                search_params.append(arr)
            else:
                const_params[k] = v

        # add a dummy variable if the grid is empty
        if len(search_params) == 0:
            search_params = [[('__dummy__', None)]]

        grid = list(itertools.product(*search_params))

        for index, cur_params in enumerate(grid):
            if verbose:
                print("done {} / {}".format(index, len(grid)), end='')
                print(" | running with ", end='')
                for k, v in cur_params:
                    if k != '__dummy__':
                        print('{}: {}\t'.format(k, v), end='')
                print('')

            cur_params = dict(cur_params)
            for k, v in const_params.items():
                cur_params[k] = v
            (cur_covs, cur_method) = self._train(train_data, cur_params, verbose)
            cur_score = utils.calculate_nll_score(data=val_data, covs=cur_covs)

            if verbose:
                print('\tcurrent score: {}'.format(cur_score))

            if (best_params is None) or (not np.isnan(cur_score) and cur_score < best_score):
                best_score = cur_score
                best_params = cur_params
                best_covs = cur_covs
                best_method = cur_method
        if verbose:
            print('\nFinished with best validation score: {}'.format(best_score))

        self._trained = True
        self._val_score = best_score
        self._params = best_params
        self._covs = best_covs
        self._method = best_method

        return best_score, best_params, best_covs, best_method

    def _train(self, train_data, params, verbose):
        # should return a pair: (covs, method)
        raise NotImplementedError()

    def evaluate(self, test_data, verbose=True):
        assert self._trained
        if verbose:
            print("Evaluating {} ...".format(self.name))
        nll = utils.calculate_nll_score(data=test_data, covs=self._covs)
        if verbose:
            print("\tScore: {:.4f}".format(nll))
        return nll

    def get_covariance(self):
        assert self._trained
        return self._covs

    def timeit(self, train_data, params):
        start_time = time.time()
        dummy = self._train(train_data, params, verbose=False)
        finish_time = time.time()
        return finish_time - start_time


class GroundTruth(Baseline):
    def __init__(self, covs, test_data, **kwargs):
        super(GroundTruth, self).__init__(**kwargs)
        self._score = utils.calculate_nll_score(data=test_data, covs=covs)
        self._covs = covs
        self._trained = True

    def _train(self, train_data, params, verbose):
        return self._covs, None


class Diagonal(Baseline):
    def __init__(self, min_var=1e-6, **kwargs):
        super(Diagonal, self).__init__(**kwargs)
        self.min_var = min_var

    def _train(self, train_data, params, verbose):
        if verbose:
            print("Training {} ...".format(self.name))
        start_time = time.time()
        covs = [np.diag(np.maximum(np.var(x, axis=0), self.min_var)) for x in train_data]
        finish_time = time.time()
        if verbose:
            print("\tElapsed time {:.1f}s".format(finish_time - start_time))
        return covs, None


class LedoitWolf(Baseline):
    def __init__(self, **kwargs):
        super(LedoitWolf, self).__init__(**kwargs)

    def _train(self, train_data, params, verbose):
        if verbose:
            print("Training {} ...".format(self.name))
        start_time = time.time()
        covs = []
        for x in train_data:
            est = sk_cov.LedoitWolf()
            est.fit(x)
            covs.append(est.covariance_)
        finish_time = time.time()
        if verbose:
            print("\tElapsed time {:.1f}s".format(finish_time - start_time))
        return covs, None


class OAS(Baseline):
    def __init__(self, **kwargs):
        super(OAS, self).__init__(**kwargs)

    def _train(self, train_data, params, verbose):
        if verbose:
            print("Training {} ...".format(self.name))
        start_time = time.time()
        covs = []
        for x in train_data:
            est = sk_cov.OAS()
            est.fit(x)
            covs.append(est.covariance_)
        finish_time = time.time()
        if verbose:
            print("\tElapsed time {:.1f}s".format(finish_time - start_time))
        return covs, None


class PCA(Baseline):
    def __init__(self, **kwargs):
        super(PCA, self).__init__(**kwargs)

    def _train(self, train_data, params, verbose):
        if verbose:
            print("Training {} ...".format(self.name))
        start_time = time.time()
        try:
            covs = []
            for x in train_data:
                est = sk_dec.PCA(n_components=params['n_components'])
                est.fit(x)
                covs.append(est.get_covariance())
        except Exception as e:
            covs = None
            if verbose:
                print("\t{} failed with message: {}".format(self.name, e.message))
        finish_time = time.time()
        if verbose:
            print("\tElapsed time {:.1f}s".format(finish_time - start_time))
        return covs, None


class SparsePCA(Baseline):
    def __init__(self, **kwargs):
        super(SparsePCA, self).__init__(**kwargs)

    def _train(self, train_data, params, verbose):
        if verbose:
            print("Training {} ...".format(self.name))
        start_time = time.time()
        try:
            covs = []
            for x in train_data:
                est = sk_dec.SparsePCA(n_components=params['n_components'],
                                       alpha=params['alpha'],
                                       ridge_alpha=params['ridge_alpha'],
                                       max_iter=params['max_iter'],
                                       tol=params['tol'])
                est.fit(x)

                # get covariance: \Psi + \Lambda.T * \Sigma_{zz} * \Lambda
                z = est.transform(x)
                cov_z = np.cov(z.T)
                var_x = np.var(x, axis=0)
                cov = np.dot(est.components_.T, np.dot(cov_z, est.components_))
                np.fill_diagonal(cov, var_x)

                covs.append(cov)
        except Exception as e:
            covs = None
            if verbose:
                print("\t{} failed with message: {}".format(self.name, e.message))
        finish_time = time.time()
        if verbose:
            print("\tElapsed time {:.1f}s".format(finish_time - start_time))
        return covs, None


class FactorAnalysis(Baseline):
    def __init__(self, **kwargs):
        super(FactorAnalysis, self).__init__(**kwargs)

    def _train(self, train_data, params, verbose):
        if verbose:
            print("Training {} ...".format(self.name))
        start_time = time.time()
        try:
            covs = []
            for x in train_data:
                est = sk_dec.FactorAnalysis(n_components=params['n_components'])
                est.fit(x)
                covs.append(est.get_covariance())
        except Exception as e:
            covs = None
            if verbose:
                print("\t{} failed with message: {}".format(self.name, e.message))
        finish_time = time.time()
        if verbose:
            print("\tElapsed time {:.1f}s".format(finish_time - start_time))
        return covs, None


class GraphLasso(Baseline):
    def __init__(self, **kwargs):
        super(GraphLasso, self).__init__(**kwargs)

    def _train(self, train_data, params, verbose):
        if verbose:
            print("Training {} ...".format(self.name))
        start_time = time.time()
        try:
            covs = []
            for x in train_data:
                est = sk_cov.GraphLasso(alpha=params['alpha'],
                                        max_iter=params['max_iter'])
                est.fit(x)
                covs.append(est.covariance_)
        except Exception as e:
            if verbose:
                print("\t{} failed with message: {}".format(self.name, e.message))
            covs = None
        finish_time = time.time()
        if verbose:
            print("\tElapsed time {:.1f}s".format(finish_time - start_time))
        return covs, None


class LinearCorex(Baseline):
    def __init__(self, **kwargs):
        super(LinearCorex, self).__init__(**kwargs)

    def _train(self, train_data, params, verbose):
        if verbose:
            print("Training {} ...".format(self.name))
        start_time = time.time()
        covs = []
        for x in train_data:
            c = linearcorex.Corex(n_hidden=params['n_hidden'],
                                  max_iter=params['max_iter'],
                                  anneal=params['anneal'])
            c.fit(x)
            covs.append(c.get_covariance())
        finish_time = time.time()
        if verbose:
            print("\tElapsed time {:.1f}s".format(finish_time - start_time))
        return covs, None


class LinearCorexWholeData(Baseline):
    def __init__(self, **kwargs):
        super(LinearCorexWholeData, self).__init__(**kwargs)

    def _train(self, train_data, params, verbose):
        if verbose:
            print("Training {} ...".format(self.name))
        start_time = time.time()
        X = np.concatenate(train_data, axis=0)
        c = linearcorex.Corex(n_hidden=params['n_hidden'],
                              max_iter=params['max_iter'],
                              anneal=params['anneal'])
        c.fit(X)
        covs = [c.get_covariance() for t in range(len(train_data))]
        finish_time = time.time()
        if verbose:
            print("\tElapsed time {:.1f}s".format(finish_time - start_time))
        return covs, c


class TimeVaryingGraphLasso(Baseline):
    def __init__(self, **kwargs):
        super(TimeVaryingGraphLasso, self).__init__(**kwargs)

    def _train(self, train_data, params, verbose):
        if verbose:
            print("Training {} ...".format(self.name))
        start_time = time.time()
        # construct time-series
        train_data_ts = []
        for x in train_data:
            train_data_ts += list(x)
        train_data_ts = np.array(train_data_ts)
        inv_covs = TVGL.TVGL(data=train_data_ts,
                             lengthOfSlice=len(train_data[0]),
                             lamb=params['lamb'],
                             beta=params['beta'],
                             indexOfPenalty=params['indexOfPenalty'],
                             max_iter=params['max_iter'])
        covs = [np.linalg.inv(x) for x in inv_covs]
        finish_time = time.time()
        if verbose:
            print("\tElapsed time {:.1f}s".format(finish_time - start_time))
        return covs, None

    def timeit(self, train_data, params):
        # need to write special timeit() to exclude the time spent for linalg.inv()
        train_data_ts = []
        for x in train_data:
            train_data_ts += list(x)
        start_time = time.time()
        train_data_ts = np.array(train_data_ts)
        inv_covs = TVGL.TVGL(data=train_data_ts,
                             lengthOfSlice=len(train_data[0]),
                             lamb=params['lamb'],
                             beta=params['beta'],
                             indexOfPenalty=params['indexOfPenalty'],
                             max_iter=params['max_iter'])
        finish_time = time.time()
        return finish_time - start_time


class TCorex(Baseline):
    def __init__(self, tcorex, **kwargs):
        self.tcorex = tcorex
        super(TCorex, self).__init__(**kwargs)

    def _train(self, train_data, params, verbose):
        if verbose:
            print("Training {} ...".format(self.name))
        start_time = time.time()

        params['nt'] = len(train_data)
        c = self.tcorex(**params)
        c.fit(train_data)
        covs = c.get_covariance()

        finish_time = time.time()
        if verbose:
            print("\tElapsed time {:.1f}s".format(finish_time - start_time))
        return covs, c

    def timeit(self, train_data, params):
        start_time = time.time()
        params['nt'] = len(train_data)
        c = self.tcorex(**params)
        c.fit(train_data)
        finish_time = time.time()
        return finish_time - start_time


class QUIC(Baseline):
    def __init__(self, **kwargs):
        super(QUIC, self).__init__(**kwargs)

    def _train(self, train_data, params, verbose):
        if verbose:
            print("Training {} ...".format(self.name))
        start_time = time.time()
        os.chdir('experiments/methods/QUIC')

        # create exp_id.m file to execute QUIC
        exp_id = random.randint(0, 2 ** 64)
        with open('{}.m'.format(exp_id), 'w') as f:
            f.write("mex -llapack QUIC.C QUIC-mex.C;\n")
            f.write("load('{}.in.mat');\n".format(exp_id))
            f.write("[X W opt cputime iter dGap] = QUIC('default', sample_cov, lamb, tol, msg, max_iter);\n")
            f.write("save('-mat', '{}.out.mat', 'X', 'W', 'opt', 'cputime', 'iter', 'dGap');\n".format(exp_id))

        covs = []
        for X in train_data:
            # create exp_id.in.mat file
            savemat('{}.in.mat'.format(exp_id), {
                'sample_cov': np.dot(X.T, X) / X.shape[0],
                'lamb': np.float(params['lamb']),
                'max_iter': params['max_iter'],
                'tol': params['tol'],
                'msg': params['msg']
            })

            # run created exp_id.m file and wait
            process = Popen(['octave', '{}.m'.format(exp_id)], stdout=PIPE, stderr=PIPE)
            process.wait()
            if verbose:
                stdout, stderr = process.communicate()
                # print("Stdout:\n{}\nStderr:\n{}".format(stdout, stderr))

            # collect outputs from exp_id.out.mat file
            outs = loadmat('{}.out.mat'.format(exp_id))
            covs.append(np.linalg.inv(outs['X']))

        # delete files and come back to the root directory
        os.remove('{}.in.mat'.format(exp_id))
        os.remove('{}.out.mat'.format(exp_id))
        os.remove('{}.m'.format(exp_id))
        os.chdir('../../..')

        finish_time = time.time()
        if verbose:
            print("\tElapsed time {:.1f}s".format(finish_time - start_time))
        return covs, None

    def timeit(self, train_data, params):
        start_time = time.time()
        os.chdir('experiments/methods/QUIC')

        # create exp_id.m file to execute QUIC
        exp_id = random.randint(0, 2 ** 64)
        with open('{}.m'.format(exp_id), 'w') as f:
            f.write("mex -llapack QUIC.C QUIC-mex.C;\n")
            f.write("load('{}.in.mat');\n".format(exp_id))
            f.write("[X W opt cputime iter dGap] = QUIC('default', sample_cov, lamb, tol, msg, max_iter);\n")
            f.write("save('-mat', '{}.out.mat', 'X', 'W', 'opt', 'cputime', 'iter', 'dGap');\n".format(exp_id))

        for X in train_data:
            # create exp_id.in.mat file
            savemat('{}.in.mat'.format(exp_id), {
                'sample_cov': np.dot(X.T, X) / X.shape[0],
                'lamb': np.float(params['lamb']),
                'max_iter': params['max_iter'],
                'tol': params['tol'],
                'msg': params['msg']
            })

            # run created exp_id.m file and wait
            process = Popen(['octave', '{}.m'.format(exp_id)], stdout=PIPE, stderr=PIPE)
            process.wait()

        # delete files and come back to the root directory
        os.remove('{}.in.mat'.format(exp_id))
        os.remove('{}.out.mat'.format(exp_id))
        os.remove('{}.m'.format(exp_id))
        os.chdir('../../..')

        finish_time = time.time()
        return finish_time - start_time


class BigQUIC(Baseline):
    def __init__(self, **kwargs):
        super(BigQUIC, self).__init__(**kwargs)

    def _train(self, train_data, params, verbose):
        if verbose:
            print("Training {} ...".format(self.name))
        start_time = time.time()
        os.chdir('experiments/methods/BigQUIC/bigquic')

        exp_id = random.randint(0, 2 ** 64)
        covs = []
        for X in train_data:
            # create exp_id.in.txt file
            with open('{}.in.txt'.format(exp_id), 'w') as f:
                f.write('{} {}\n'.format(X.shape[1], X.shape[0]))
                for x in X:
                    f.write(' '.join(['{:.9f}'.format(t) for t in x]) + '\n')

            # build exp_id.sh file
            with open('{}.sh'.format(exp_id), 'w') as f:
                f.write('./bigquic-run -l {} -t {} -q {} -e {} {}.in.txt {}.out.txt;\n'.format(
                    params['lamb'],
                    params['max_iter'],
                    params['verbose'],
                    params['tol'],
                    exp_id,
                    exp_id
                ))

            # run created exp_id.m file and wait
            process = Popen(['bash', '{}.sh'.format(exp_id)], stdout=PIPE, stderr=PIPE)
            process.wait()
            if verbose:
                stdout, stderr = process.communicate()
                # print("Stdout:\n{}\nStderr:\n{}".format(stdout, stderr))

            # collect outputs from exp_id.out.txt file
            nv = X.shape[1]
            precision_mat = np.zeros((nv, nv))
            with open('{}.out.txt'.format(exp_id), 'r') as f:
                ret = re.search('p: ([0-9]+), nnz: ([0-9]+)', f.readline())
                p = int(ret.group(1))
                non_zero = int(ret.group(2))
                assert p == nv
                for i in range(non_zero):
                    mas = f.readline().split(' ')
                    row, col = map(int, mas[:2])
                    value = float(mas[2])
                    precision_mat[row-1, col-1] = value

            covs.append(np.linalg.inv(precision_mat))

        # delete files and come back to the root directory
        os.remove('{}.in.txt'.format(exp_id))
        os.remove('{}.out.txt'.format(exp_id))
        os.remove('{}.sh'.format(exp_id))
        os.chdir('../../../../')

        finish_time = time.time()
        if verbose:
            print("\tElapsed time {:.1f}s".format(finish_time - start_time))
        return covs, None

    def timeit(self, train_data, params):
        start_time = time.time()
        os.chdir('experiments/methods/BigQUIC/bigquic')

        exp_id = random.randint(0, 2 ** 64)
        for X in train_data:
            # create exp_id.in.txt file
            with open('{}.in.txt'.format(exp_id), 'w') as f:
                f.write('{} {}\n'.format(X.shape[1], X.shape[0]))
                for x in X:
                    f.write(' '.join(['{:.9f}'.format(t) for t in x]) + '\n')

            # build exp_id.sh file
            with open('{}.sh'.format(exp_id), 'w') as f:
                f.write('./bigquic-run -l {} -t {} -q {} -e {} {}.in.txt {}.out.txt;\n'.format(
                    params['lamb'],
                    params['max_iter'],
                    params['verbose'],
                    params['tol'],
                    exp_id,
                    exp_id
                ))

            # run created exp_id.m file and wait
            process = Popen(['bash', '{}.sh'.format(exp_id)], stdout=PIPE, stderr=PIPE)
            process.wait()

        # delete files and come back to the root directory
        os.remove('{}.in.txt'.format(exp_id))
        os.remove('{}.out.txt'.format(exp_id))
        os.remove('{}.sh'.format(exp_id))
        os.chdir('../../../../')

        finish_time = time.time()
        return finish_time - start_time
