# @title Defining a time-dependent score-based model (double click to expand or collapse)

import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np


class GaussianFourierProjection(nn.Module):
    """Gaussian random features for encoding time steps."""

    def __init__(self, embed_dim, scale=30.):
        super().__init__()
        # Randomly sample weights during initialization. These weights are fixed

        # during optimization and are not trainable.
        self.W = nn.Parameter(torch.randn(embed_dim // 2) * scale, requires_grad=False)

    def forward(self, x):
        #x_proj = x[:,:, None] * self.W[None,None, :] * 2 * np.pi
        x_proj = x[:, None] * self.W[None, :] * 2 * np.pi
        return torch.cat([torch.sin(x_proj), torch.cos(x_proj)], dim=-1)

'''
class CosineActivation(nn.Module):
    def __init__(self, alpha=1.0, beta=0.0):
        super(CosineActivation, self).__init__()
        self.alpha = alpha
        self.beta = beta

    def forward(self, x):
        return torch.cos(self.alpha * x + self.beta)
'''

class Dense(nn.Module):
    """A fully connected layer that reshapes outputs to feature maps."""

    def __init__(self, input_dim, output_dim):
        super().__init__()
        self.dense = nn.Linear(input_dim, output_dim)

    def forward(self, x):
        #x=x.view(x.shape[0],-1)
        return self.dense(x)[..., None, None]


class ScoreNet(nn.Module):
    """A time-dependent score-based model built upon U-Net architecture."""

    def __init__(self, marginal_prob_std, channels=[32, 64, 128, 256], embed_dim=256):
        """Initialize a time-dependent score-based network.

    Args:
      marginal_prob_std: A function that takes time t and gives the standard
        deviation of the perturbation kernel p_{0t}(x(t) | x(0)).
      channels: The number of channels for feature maps of each resolution.
      embed_dim: The dimensionality of Gaussian random feature embeddings.
    """
        super().__init__()
        # Gaussian random feature embedding layer for time
        self.embed = nn.Sequential(GaussianFourierProjection(embed_dim=embed_dim),
                                   nn.Linear(embed_dim, embed_dim))
        # Encoding layers where the resolution decreases
        self.conv1 = nn.Conv2d(2, channels[0], kernel_size=3, stride=1, padding=0, bias=False)
        self.dense1 = Dense(embed_dim, channels[0])
        self.gnorm1 = nn.GroupNorm(4, num_channels=channels[0])
        self.conv2 = nn.Conv2d(channels[0], channels[1], kernel_size=3, stride=1, padding=0, bias=False)
        self.dense2 = Dense(embed_dim, channels[1])
        self.gnorm2 = nn.GroupNorm(32, num_channels=channels[1])
        self.conv3 = nn.Conv2d(channels[1], channels[2], kernel_size=3, stride=1, padding=0, bias=False)
        self.dense3 = Dense(embed_dim, channels[2])
        self.gnorm3 = nn.GroupNorm(32, num_channels=channels[2])
        self.conv4 = nn.Conv2d(channels[2], channels[3], kernel_size=3, stride=1, padding=0, bias=False)
        self.dense4 = Dense(embed_dim, channels[3])
        self.gnorm4 = nn.GroupNorm(32, num_channels=channels[3])

        # Decoding layers where the resolution increases
        self.tconv4 = nn.ConvTranspose2d(channels[3], channels[2], kernel_size=3, stride=1, padding=2, bias=False)
        self.dense5 = Dense(embed_dim, channels[2])
        self.tgnorm4 = nn.GroupNorm(32, num_channels=channels[2])
        self.tconv3 = nn.ConvTranspose2d(channels[2] + channels[2], channels[1], kernel_size=3, stride=1, padding=2,
                                         bias=False)
        self.dense6 = Dense(embed_dim, channels[1])
        self.tgnorm3 = nn.GroupNorm(32, num_channels=channels[1])
        self.tconv2 = nn.ConvTranspose2d(channels[1] + channels[1], channels[0], kernel_size=3, stride=1, padding=2,
                                         bias=False)
        self.dense7 = Dense(embed_dim, channels[0])
        self.tgnorm2 = nn.GroupNorm(32, num_channels=channels[0])
        self.tconv1 = nn.ConvTranspose2d(channels[0] + channels[0], 2, kernel_size=3, stride=1, padding=2)

        # The swish activation function
        self.act = lambda x: x * torch.sigmoid(x)
        #self.act = CosineActivation(1,0)
        self.marginal_prob_std = marginal_prob_std

    def circular_padding(self, x, padding):
        return F.pad(x, pad=(padding, padding, padding, padding), mode='circular')

    def forward(self, x, t):
        # Obtain the Gaussian random feature embedding for t
        embed = self.act(self.embed(t))
        # Encoding path
        h1 = self.circular_padding(x, 1)
        h1 = self.conv1(h1)
        ## Incorporate information from t
        h1 += self.dense1(embed)
        ## Group normalization
        h1 = self.gnorm1(h1)
        h1 = self.act(h1)


        h2 = self.circular_padding(h1, 1)
        h2 = self.conv2(h2)

        h2 += self.dense2(embed)
        h2 = self.gnorm2(h2)
        h2 = self.act(h2)


        h3 = self.circular_padding(h2, 1)
        h3 = self.conv3(h3)
        h3 += self.dense3(embed)
        h3 = self.gnorm3(h3)
        h3 = self.act(h3)


        h4 = self.circular_padding(h3, 1)
        h4 = self.conv4(h4)
        h4 += self.dense4(embed)
        h4 = self.gnorm4(h4)
        h4 = self.act(h4)


        # Decoding path
        h_4 = self.circular_padding(h4, 1)
        h = self.tconv4(h_4)
        ## Skip connection from the encoding path
        h += self.dense5(embed)
        h = self.tgnorm4(h)
        h = self.act(h)

        h_3= torch.cat([h, h3], dim=1)
        h_3= self.circular_padding(h_3, 1)
        h = self.tconv3(h_3)
        h += self.dense6(embed)
        h = self.tgnorm3(h)
        h = self.act(h)

        h_2 = torch.cat([h, h2], dim=1)
        h_2 = self.circular_padding(h_2, 1)
        h = self.tconv2(h_2)
        h += self.dense7(embed)
        h = self.tgnorm2(h)
        h = self.act(h)

        h_1 = torch.cat([h, h1], dim=1)
        h_1 = self.circular_padding(h_1, 1)
        h = self.tconv1(h_1)

        # Normalize output
        h = h / self.marginal_prob_std(t)[:, None, None, None]
        return h
