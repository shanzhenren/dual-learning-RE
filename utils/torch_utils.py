"""
Utility functions for torch.
"""
import torch
from torch.optim import Optimizer
import numpy as np

# class


class MyAdagrad(Optimizer):
    """My modification of the Adagrad optimizer that allows to specify an initial
    accumulater value. This mimics the behavior of the default Adagrad implementation
    in Tensorflow. The default PyTorch Adagrad uses 0 for initial acculmulator value.

    Arguments:
        params (iterable): iterable of parameters to optimize or dicts defining
            parameter groups
        lr (float, optional): learning rate (default: 1e-2)
        lr_decay (float, optional): learning rate decay (default: 0)
        init_accu_value (float, optional): initial accumulater value.
        weight_decay (float, optional): weight decay (L2 penalty) (default: 0)
    """

    def __init__(self, params, lr=1e-2, lr_decay=0, init_accu_value=0.1, weight_decay=0):
        defaults = dict(lr=lr, lr_decay=lr_decay, init_accu_value=init_accu_value, weight_decay=weight_decay)
        super(MyAdagrad, self).__init__(params, defaults)

        for group in self.param_groups:
            for p in group["params"]:
                state = self.state[p]
                state["step"] = 0
                state["sum"] = torch.ones(p.data.size()).type_as(p.data) * init_accu_value

    def share_memory(self):
        for group in self.param_groups:
            for p in group["params"]:
                state = self.state[p]
                state["sum"].share_memory_()

    def step(self, closure=None):
        """Performs a single optimization step.

        Arguments:
            closure (callable, optional): A closure that reevaluates the model
                and returns the loss.
        """
        loss = None
        if closure is not None:
            loss = closure()

        for group in self.param_groups:
            for p in group["params"]:
                if p.grad is None:
                    continue

                grad = p.grad.data
                state = self.state[p]

                state["step"] += 1

                if group["weight_decay"] != 0:
                    if p.grad.data.is_sparse:
                        raise RuntimeError("weight_decay option is not compatible with sparse gradients ")
                    grad = grad.add(group["weight_decay"], p.data)

                clr = group["lr"] / (1 + (state["step"] - 1) * group["lr_decay"])

                if p.grad.data.is_sparse:
                    grad = grad.coalesce()  # the update is non-linear so indices must be unique
                    grad_indices = grad._indices()
                    grad_values = grad._values()
                    size = torch.Size([x for x in grad.size()])

                    def make_sparse(values):
                        constructor = type(p.grad.data)
                        if grad_indices.dim() == 0 or values.dim() == 0:
                            return constructor()
                        return constructor(grad_indices, values, size)

                    state["sum"].add_(make_sparse(grad_values.pow(2)))
                    std = state["sum"]._sparse_mask(grad)
                    std_values = std._values().sqrt_().add_(1e-10)
                    p.data.add_(-clr, make_sparse(grad_values / std_values))
                else:
                    state["sum"].addcmul_(1, grad, grad)
                    std = state["sum"].sqrt().add_(1e-10)
                    p.data.addcdiv_(-clr, grad, std)

        return loss


# torch specific functions


def get_optimizer(name, parameters, lr):
    name = name.strip()
    if name == "sgd":
        return torch.optim.SGD(parameters, lr=lr)
    elif name in ["adagrad", "myadagrad"]:
        # use my own adagrad to allow for init accumulator value
        return MyAdagrad(parameters, lr=lr, init_accu_value=0.1)
    elif name == "adam":
        return torch.optim.Adam(parameters, lr=lr, betas=(0.9, 0.99))  # use default lr
    elif name == "adamax":
        return torch.optim.Adamax(parameters)  # use default lr
    else:
        raise Exception("Unsupported optimizer: {}".format(name))


def change_lr(optimizer, new_lr):
    for param_group in optimizer.param_groups:
        param_group["lr"] = new_lr


def flatten_indices(seq_lens, width):
    flat = []
    for i, l in enumerate(seq_lens):
        for j in range(l):
            flat.append(i * width + j)
    return flat


def set_cuda(var, cuda):
    if cuda:
        return var.cuda()
    return var


def keep_partial_grad(grad, topk):
    """
    Keep only the topk rows of grads.
    """
    assert topk < grad.size(0)
    grad.data[topk:].zero_()
    return grad


def unsort_idx(examples, batch_size):
    def unsort(lengths):
        return sorted(range(len(lengths)), key=lengths.__getitem__, reverse=True)

    lengths = np.array([len(ex.token) for ex in examples])
    # idxs = [np.argsort(np.argsort(- lengths[i:i+batch_size])) for i in range(0, len(lengths), batch_size)]
    idxs = [np.argsort(unsort(lengths[i : i + batch_size])) for i in range(0, len(lengths), batch_size)]
    return [torch.LongTensor(idx) for idx in idxs]


# model IO


def save(model, optimizer, opt, filename):
    params = {"model": model.state_dict(), "optimizer": optimizer.state_dict(), "config": opt}
    try:
        torch.save(params, filename)
    except BaseException:
        print("[ Warning: model saving failed. ]")


def load(model, optimizer, filename):
    try:
        dump = torch.load(filename)
    except BaseException:
        print("[ Fail: model loading failed. ]")
    if model is not None:
        model.load_state_dict(dump["model"])
    if optimizer is not None:
        optimizer.load_state_dict(dump["optimizer"])
    opt = dump["config"]
    return model, optimizer, opt


def load_config(filename):
    dump = torch.load(filename)
    return dump["config"]


# data batch


def batch_to_input(batch, vocab_pad_id=0):
    inputs = {}
    inputs["words"], inputs["length"] = batch.token
    inputs["pos"] = batch.pos
    inputs["ner"] = batch.ner
    inputs["subj_pst"] = batch.subj_pst
    inputs["obj_pst"] = batch.obj_pst
    inputs["masks"] = torch.eq(batch.token[0], vocab_pad_id)
    inputs["pr_confidence"] = batch.pr_confidence
    inputs["sl_confidence"] = batch.sl_confidence
    return inputs, batch.relation


def example_to_dict(example, pr_confidence, sl_confidence, rel):
    output = {}
    output["tokens"] = example.token
    output["stanford_pos"] = example.pos
    output["stanford_ner"] = example.ner
    output["subj_pst"] = example.subj_pst
    output["obj_pst"] = example.obj_pst
    output["relation"] = rel
    output["pr_confidence"] = pr_confidence
    output["sl_confidence"] = sl_confidence
    return output


def arg_max(l):
    bvl, bid = -1, -1
    for k in range(len(l)):
        if l[k] > bvl:
            bvl = l[k]
            bid = k
    return bid, bvl
