import gflags
import logging
import numpy as np

from needle.agents import BasicAgent, register_agent
from needle.agents.TNPG.net import Net
from needle.helper.conjugate_gradient import conjugate_gradient
from needle.helper.OU_process import OUProcess
from needle.helper.softmax_sampler import SoftmaxSampler
from needle.helper.batcher import Batcher

# if program encounters NaN, decrease this value
gflags.DEFINE_float("delta_KL", 0.01, "KL divergence between two sets of parameters")
FLAGS = gflags.FLAGS

line_search_decay = 0.5


def get_matrix(model, states, choices, advantages, num_parameters):
    func = lambda direction: model.fisher_vector_product(direction, [states], [choices], [advantages])
    A = np.zeros((num_parameters, num_parameters))
    k = np.zeros(num_parameters)
    for i in range(num_parameters):
        k[i] = 1.
        A[:, i] = func(k)
        k[i] = 0.
    # s, v, d = np.linalg.svd(A)
    # logging.debug("singular values of A = %s" % (v,))
    # logging.debug("A = %s" % (A,))
    return A


@register_agent("TNPG")
class Agent(SoftmaxSampler, Batcher, BasicAgent):
    def __init__(self, input_dim, output_dim):
        self.input_dim = input_dim
        self.output_dim = output_dim
        self.counter = 0

        self.net = Net(input_dim, output_dim)
        self.net.build_infer()
        self.net.build_train()

        self.baseline = 20

    def train_batch(self, lengths, mask, states, choices, rewards, new_states):
        # logging.info("lengths = %s, mask = %s, states = %s, choices = %s, rewards = %s, new_states = %s" %
        #              (lengths.shape, mask.shape, states.shape, choices.shape, rewards.shape, new_states.shape))

        # old_logits = self.model.infer([states])
        # logging.debug("old logits = %s" % (old_logits,))
        # weight = old_var[:8].reshape([4, 2])
        # bias = old_var[8:].reshape([2])
        #
        # logging.info("flat grad = %s" % tf.get_default_session().run(self.model.op_flat_gradient, feed_dict={
        #     self.model.op_inputs: [states],
        # }))
        # g = np.zeros((4, 2))
        # for i in range(len(rewards)):
        #     pi = softmax(states[i].dot(weight) + bias)
        #     logging.info(pi)
        #     g += states[i].reshape(4, 1).dot((pi * (1 - pi)).reshape(1, 2))
        # logging.info("computed = %s" % (g))

        # for i in reversed(range(num_timesteps - 1)):
        #     advantages[i] += advantages[i + 1] * FLAGS.gamma
        # advantages = rewards * (np.expand_dims(lengths, 1))
        advantages = np.cumsum(rewards[:, ::-1], axis=1)[:, ::-1]
        # logging.info("mask = %s" % (mask,))
        # logging.info("advantages = %s, rewards = %s" % (advantages[0], rewards[0]))
        feed_dict = self.net.get_dict(lengths, mask, states, choices, advantages)

        self.baseline = self.baseline * 0.9 + np.mean(lengths) * 0.1

        gradient = self.net.gradient(feed_dict)
        # old_loss = self.model.get_loss([states], [choices], [advantages])
        # logging.info("old loss = %s" % (old_loss,))

        # T = get_matrix(self.model, states, choices, advantages, 10)
        # logging.debug("T = %s" % (np.linalg.inv(T),))

        natural_gradient, dot_prod = conjugate_gradient(
            lambda direction: self.net.fisher_vector_product(direction, feed_dict),
            gradient,
        )
        natural_gradient *= np.sqrt(2 * FLAGS.delta_KL / (dot_prod + 1e-8))
        variables = self.net.get_variables()

        # logging.debug("gradient = %s" % (gradient,))
        # natural_gradient *= 0.1
        # natural_gradient = gradient * 0.01

        # logging.info("step size = %s" % (step_size,))
        # new_logits = self.model.infer([states])
        # logging.debug("new logits = %s" % (new_logits,))

        # logging.debug("xAx = %s, natgrad dot grad = %s" % (dot_prod, natural_gradient.dot(gradient)))
        # logging.debug("gradient  = %s" % (gradient,))
        # logging.debug("natgrad   = %s" % (natural_gradient,))
        # logging.info("variables = %s" % (variables,))

        old_loss, old_KL, old_actions = self.net.test(feed_dict)
        logging.info("old loss = %s, old KL = %s" % (old_loss, np.mean(old_KL)))

        # new_loss, new_KL, new_actions = self.model.test(feed_dict, old_actions=old_actions)
        # logging.info("new loss = %s, new KL = %s" % (new_loss, np.mean(new_KL)))
        #
        while True:
            self.net.apply_var(variables - natural_gradient)
            new_loss, new_KL, new_actions = self.net.test(feed_dict, old_actions=old_actions)
            logging.info("new loss = %s, new KL = %s" % (new_loss, np.mean(new_KL)))
            # logging.debug("new variables %s" % (var,))
            # KL_divergence = np.mean(np.sum(old_actions * np.log(old_actions / new_actions), axis=2))
            # logging.debug("    variables %s" % (variables - natural_gradient,))
            # logging.debug("old_actions = %s" % (old_actions[0].T))
            # logging.debug("new_actions = %s" % (new_actions[0].T))
            # logging.debug("shape = %s" % (np.sum(old_actions * np.log(old_actions / new_actions), axis=2).shape,))

            # logging.info("new loss = %s, KL divergence = %s" % (new_loss, new_KL - old_KL))
            if new_KL - old_KL <= FLAGS.delta_KL and new_loss <= old_loss:
                break
            # self.net.apply_grad(natural_gradient * (line_search_decay - 1))
            natural_gradient *= line_search_decay

        # self.model.apply_delta(-natural_gradient)
        # self.model.train(natural_gradient)  # TODO: check if it is SGD

        # old_dist = softmax(old_logits)
        # new_dist = softmax(new_logits)
        # logging.info("KL divergence = %s" % (np.mean(np.sum(old_dist * np.log(old_dist / new_dist), axis=-1))))

    def action(self, inputs):
        return self.softmax_action(
            self.net.infer(np.array([inputs])),
            noise=self.noise,
        )

    def reset(self):
        self.noise = OUProcess()
        self.net.reset()
        self.counter = 0
