"Input pipeline for topsort DAG data"

import linecache, json
import collections
import numpy as np

import torch
import torch.utils.data as data

from os import listdir
from os.path import isfile, join


class DynamicBatchDivider(object):
    def __init__(self, limit, hidden_dim):
        self.limit = limit
        self.hidden_dim = hidden_dim

    def divide(self, variable_num, function_num, graph_map, edge_feature, graph_feature, label):
        batch_size = len(variable_num)
        edge_num = [len(n) for n in edge_feature]

        graph_map_list = []
        edge_feature_list = []
        graph_feature_list = []
        variable_num_list = []
        function_num_list = []
        label_list = []

        if (self.limit // (max(edge_num) * self.hidden_dim)) >= batch_size:
            if graph_feature[0] is None:
                graph_feature_list = [[None]]
            else:
                graph_feature_list = [graph_feature]

            graph_map_list = [graph_map]
            edge_feature_list = [edge_feature]
            variable_num_list = [variable_num]
            function_num_list = [function_num]
            label_list = [label]

        else:

            indices = sorted(range(len(edge_num)), reverse=True, key=lambda k: edge_num[k])
            sorted_edge_num = sorted(edge_num, reverse=True)

            i = 0

            while i < batch_size:
                allowed_batch_size = self.limit // (sorted_edge_num[i] * self.hidden_dim)
                ind = indices[i:min(i + allowed_batch_size, batch_size)]

                if graph_feature[0] is None:
                    graph_feature_list += [[None]]
                else:
                    graph_feature_list += [[graph_feature[j] for j in ind]]

                edge_feature_list += [[edge_feature[j] for j in ind]]
                variable_num_list += [[variable_num[j] for j in ind]]
                function_num_list += [[function_num[j] for j in ind]]
                graph_map_list += [[graph_map[j] for j in ind]]
                label_list += [[label[j] for j in ind]]

                i += allowed_batch_size

        return variable_num_list, function_num_list, graph_map_list, edge_feature_list, graph_feature_list, label_list

    # def divide(self, graph, node, label, pred, pred_length, edge_count):
    #     batch_size = len(node)
    #     length = [n.size()[0] for n in node]
    #     indices = sorted(range(len(length)), reverse=True, key=lambda k: length[k])
    #     sorted_length = sorted(length, reverse=True)
    #     length_cum_sum = np.cumsum(sorted_length)

    #     graph_list = []
    #     node_list = []
    #     label_list = []
    #     pred_list = []
    #     pred_length_list = []
    #     edge_count_list = []

    #     i = 0
    #     value = self.limit // self.hidden_dim

    #     while i < batch_size:
    #         k = np.searchsorted(length_cum_sum, value, side='right')
    #         ind = indices[i:min(k, batch_size)]

    #         if graph[0] is None:
    #             graph_list += [[None]]
    #         else:
    #             graph_list += [[graph[j] for j in ind]]

    #         node_list += [[node[j] for j in ind]]
    #         label_list += [[label[j] for j in ind]]
    #         pred_list += [[pred[j] for j in ind]]
    #         pred_length_list += [[pred_length[j] for j in ind]]
    #         edge_count_list += [[edge_count[j] for j in ind]]

    #         i = k
    #         value = length_cum_sum[min(k - 1, batch_size)] + self.limit // self.hidden_dim

    #     return graph_list, node_list, label_list, pred_list, pred_length_list, edge_count_list


class FactorGraphDataset(data.Dataset):

    # batch_divider = DynamicBatchDivider(3000000, 100)
    # batch_divider = DynamicBatchDivider(3500000, 100)
    # batch_divider = DynamicBatchDivider(40000000, 100)
    batch_divider = DynamicBatchDivider(4000000, 150)
    # batch_divider = DynamicBatchDivider(1800000, 100)

    def __init__(self, input_file, max_cache_size=100000, generator=None, epoch_size=0):

        self._cache = collections.OrderedDict()
        self._generator = generator
        self._epoch_size = epoch_size
        self._input_file = input_file
        self._max_cache_size = max_cache_size

        if self._generator is None:
            with open(self._input_file, 'r') as fh_input:
                self._row_num = len(fh_input.readlines()) - 1

    def __len__(self):
        if self._generator is not None:
            return self._epoch_size
        else:
            return self._row_num

    def __getitem__(self, idx):
        if self._generator is not None:
            return self._generator.generate()

        else:
            if idx in self._cache:
                return self._cache[idx]

            line = linecache.getline(self._input_file, idx + 1)
            result = self._convert_line(line)

            if len(self._cache) >= self._max_cache_size:
                self._cache.popitem(last=False)

            self._cache[idx] = result
            return result

    def _convert_line(self, json_str):

        input_data = json.loads(json_str)
        variable_num, function_num = input_data[0]

        variable_ind = np.abs(np.array(input_data[1], dtype=np.int32)) - 1
        function_ind = np.abs(np.array(input_data[2], dtype=np.int32)) - 1
        edge_feature = np.sign(np.array(input_data[1], dtype=np.float32))

        graph_map = np.stack((variable_ind, function_ind))
        alpha = float(function_num) / variable_num

        # return (variable_num, function_num, graph_map, edge_feature, [alpha], float(input_data[3]))
        return (variable_num, function_num, graph_map, edge_feature, None, float(input_data[3]))


    @staticmethod
    def dag_collate_fn(input_data):
        "Torch dataset loader collation function for DAG input."

        vn, fn, gm, ef, gf, l = zip(*input_data)

        variable_num, function_num, graph_map, edge_feature, graph_feat, label = \
            FactorGraphDataset.batch_divider.divide(vn, fn, gm, ef, gf, l)
        segment_num = len(variable_num)

        graph_feat_batch = []
        graph_map_batch = []
        batch_variable_map_batch = []
        batch_function_map_batch = []
        edge_feature_batch = []
        label_batch = []

        for i in range(segment_num):

            # Create the graph features batch
            graph_feat_batch += [None if graph_feat[i][0] is None else torch.from_numpy(np.stack(graph_feat[i])).float()]

            # Create the edge feature batch
            edge_feature_batch += [torch.from_numpy(np.expand_dims(np.concatenate(edge_feature[i]), 1)).float()]

            # Create the label batch
            label_batch += [torch.from_numpy(np.expand_dims(np.array(label[i]), 1)).float()]

            # Create the graph map, variable map and function map batches
            g_map_b = np.zeros((2, 0), dtype=np.int32)
            v_map_b = np.zeros(0, dtype=np.int32)
            f_map_b = np.zeros(0, dtype=np.int32)
            variable_ind = 0
            function_ind = 0

            for j in range(len(graph_map[i])):
                graph_map[i][j][0, :] += variable_ind
                graph_map[i][j][1, :] += function_ind
                g_map_b = np.concatenate((g_map_b, graph_map[i][j]), axis=1)

                v_map_b = np.concatenate((v_map_b, np.tile(j, variable_num[i][j])))
                f_map_b = np.concatenate((f_map_b, np.tile(j, function_num[i][j])))

                variable_ind += variable_num[i][j]
                function_ind += function_num[i][j]

            graph_map_batch += [torch.from_numpy(g_map_b).int()]
            batch_variable_map_batch += [torch.from_numpy(v_map_b).int()]
            batch_function_map_batch += [torch.from_numpy(f_map_b).int()]

        return graph_map_batch, batch_variable_map_batch, batch_function_map_batch, edge_feature_batch, graph_feat_batch, label_batch

    @staticmethod
    def get_loader(input_file, limit, hidden_dim, batch_size, shuffle, num_workers,
                    max_cache_size=100000, use_cuda=True, generator=None, epoch_size=0):
        "Return the torch dataset loader object for the input."

        FactorGraphDataset.batch_divider = DynamicBatchDivider(limit, hidden_dim)        

        dataset = FactorGraphDataset(
            input_file=input_file,
            max_cache_size=max_cache_size,
            generator=generator, 
            epoch_size=epoch_size)

        data_loader = torch.utils.data.DataLoader(
            dataset=dataset,
            batch_size=batch_size,
            shuffle=shuffle,
            num_workers=num_workers,
            collate_fn=FactorGraphDataset.dag_collate_fn,
            pin_memory=use_cuda)

        return data_loader


if __name__ == '__main__':
    loader = FactorGraphDataset.get_loader(input_file='../../../datasets/SAT/toy.json', 
        limit=500, hidden_dim=1, batch_size=3, shuffle=False, num_workers=1,
                    max_cache_size=100000, use_cuda=False, generator=None, epoch_size=0)

    for (j, data) in enumerate(loader, 1):
        for d in data:
            print(d)

        break





