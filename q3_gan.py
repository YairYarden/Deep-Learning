# -*- coding: utf-8 -*-
"""Q3_GAN.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/157IzswJcXocMIS4BLOvHBnz8txybx7nV

# Imports
"""

# Commented out IPython magic to ensure Python compatibility.
import numpy as np
from datetime import datetime
import time
import os

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, SubsetRandomSampler
from torch.utils.tensorboard import SummaryWriter
from torch import autograd

import torchvision
from torchvision import datasets, transforms

import matplotlib.pyplot as plt

from sklearn.svm import SVC # For SVM

# %load_ext tensorboard

# check if CUDA is available
is_gpu_available = torch.cuda.is_available()

if not is_gpu_available:
    print('CUDA is not available.  Training on CPU ...')
else:
    print('CUDA is available!  Training on GPU ...')

DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'

DIR_PATH = '/content/drive/MyDrive/Deep_learning_05107255/ex3_316168061_313471526'

"""# Mount Google Drive"""

from google.colab import drive
drive.mount('/content/drive/')

drive_path = '/content/drive/MyDrive/Deep_learning_05107255/ex3_316168061_313471526'

# Path for check point
ckpts_path = os.path.join(drive_path, 'Q3_GAN', 'checkpoints')
os.makedirs(ckpts_path, exist_ok=True)

print('Drive Path : ' + drive_path)
print('Check points Path : ' + ckpts_path)

"""# Define models Consts"""

DIM = 64
BATCH_SIZE = 64
NOISE_SIZE = 128
GEN_ITER = 20000
DISC_ITER = 5
IMAGE_SIZE = 28
OUTPUT_TOT_SIZE = 784  # Fashion MNIST size (28, 28)
IMAGE_CHANNELS = 1
LAMBDA = 10 # Gradient penalty hyper-parameter

"""# Define GAN Model"""

from torch.nn.modules.batchnorm import BatchNorm2d


class Generator(nn.Module):
    def __init__(self, mode, kernel_size_1=5, kernel_size_2=5):
        super(Generator, self).__init__()
        self.mode = mode
        self.fc1 = nn.Sequential(
            nn.Linear(NOISE_SIZE, 4 * 4 * 4 * DIM),
            nn.BatchNorm2d(4 * 4 * 4 * DIM) if mode == 'wgan' else nn.Identity(),
            nn.ReLU(True)
            )

        self.pre_activate1 = nn.Sequential(
            nn.ConvTranspose2d(4 * DIM, 2 * DIM, kernel_size_1),
            nn.BatchNorm2d(2 * DIM),
            nn.ReLU(True)
        )

        self.pre_activate2 = nn.Sequential(
            nn.ConvTranspose2d(2 * DIM, DIM, kernel_size_2),
            nn.BatchNorm2d(2 * DIM),
            nn.ReLU(True)
        )

        self.pre_activate3 = nn.ConvTranspose2d(DIM, IMAGE_CHANNELS, stride=2)

        self.sigmoid = nn.Sigmoid

    def forward(self, input):
        output = self.fc1(input)
        output = output.view(-1, 4 * DIM, 4, 4)
        output = self.pre_activate1(output)
        print(output.size())
        output = output[:, :, :7, :7]
        print(output.size())
        output = self.pre_activate2(output)
        print(output.size())
        output = self.pre_activate3(output)
        print(output.size())
        output = self.sigmoid(output)
        return output.view(-1, OUTPUT_TOT_SIZE)


class Discriminator(nn.Module):
    def __init__(self, kernel_size=5):
        super(Discriminator, self).__init__()

        self.fc1 = nn.Sequential(
            nn.Linear(IMAGE_CHANNELS, DIM, kernel_size, stride=2, padding=2),
            nn.LeakyReLU(True)
        )

        self.fc2 = nn.Sequential(
            nn.Linear(DIM, 2 * DIM, kernel_size, stride=2, padding=2),
            nn.LeakyReLU(True)
        )

        self.fc3 = nn.Sequential(
            nn.Linear(2 * DIM, 4 * DIM, kernel_size, stride=2, padding=2),
            nn.LeakyReLU(True)
        )

        self.fc4 = nn.Linear(4 * 4 * 4 * DIM, 1)

    def forward(self, input):
        output = self.fc1(input)
        output = self.fc2(output)
        output = self.fc3(output)
        output = output.view(-1, 4 * 4 * 4 * DIM)
        output = self.fc4(output)
        return output.view(-1)


def calc_gradient_penalty(disc_net, real_data, fake_data):
    alpha = torch.rand(BATCH_SIZE, 1)
    alpha = alpha.expand(real_data.size())
    alpha = alpha.to(DEVICE) if is_gpu_available else alpha

    interpolates = alpha * real_data + ((1 - alpha) * fake_data)

    if is_gpu_available:
        interpolates = interpolates.to(DEVICE)
    interpolates = autograd.Variable(interpolates, requires_grad=True)

    disc_interpolates = disc_net(interpolates)

    gradients = autograd.grad(outputs=disc_interpolates, inputs=interpolates,
                              grad_outputs=torch.ones(disc_interpolates.size()).to(DEVICE) if is_gpu_available
                              else torch.ones(disc_interpolates.size()),
                              create_graph=True, retain_graph=True, only_inputs=True)[0]

    gradient_penalty = ((gradients.norm(2, dim=1) - 1) ** 2).mean() * LAMBDA
    return gradient_penalty


def generate_fake_image(gen_net):
    noise = torch.randn(BATCH_SIZE, NOISE_SIZE)
    if is_gpu_available:
        noise = noise.to(DEVICE)
    noise_var = autograd.Variable(noise, volatile=True)
    fake_images = gen_net(noise_var)
    fake_images = fake_images.view(BATCH_SIZE, IMAGE_SIZE, IMAGE_SIZE)
    return fake_images


def shuffle_data(images, labels):
    assert len(images) == len(labels)
    p = np.random.permutation(len(labels))
    return images[p], labels[p]


def data_generator(data, batch_size):
    images, labels = data
    images, labels = shuffle_data(images, labels)
    image_batches = images.reshape(-1, batch_size, OUTPUT_TOT_SIZE)
    label_batches = labels.reshape(-1, batch_size)

    for i in range(len(image_batches)):
        yield (np.copy(image_batches[i]), np.copy(label_batches[i]))


def images_train_gen(data_gen):
    while True:
        for images, labels in data_gen():
            yield images


def train_disc_net(gen_net, disc_net, real_data_gen, disc_opt, use_gradient_penalty=True):

    for disc_iter in range(DISC_ITER):

        # Train with real data
        real_data = torch.Tensor(real_data_gen.next())
        if torch.cuda.is_available():
            real_data = real_data.to(DEVICE)
        real_data_var = autograd.Variable(real_data)

        disc_net.zero_grad()

        disc_out_real = disc_net(real_data_var)
        disc_out_real = disc_out_real.mean()
        disc_out_real.backward(torch.FloatTensor([-1].to(DEVICE)))

        # Train with fake data
        noise = torch.randn(BATCH_SIZE, NOISE_SIZE)
        if torch.cuda.is_available():
            noise = noise.to(DEVICE)
        noise_var = autograd.Variable(noise, volatile=True)
        fake_images = gen_net(noise_var)
        fake_images = autograd.Variable(fake_images.data)
        disc_in_images = fake_images
        disc_out_fake = disc_net(disc_in_images)
        disc_out_fake = disc_out_fake.mean()
        disc_out_fake.backward(torch.FloatTensor([1].to(DEVICE)))

        # train with gradient penalty
        if use_gradient_penalty:
            gradient_penalty = calc_gradient_penalty(disc_net, real_data_var.data, fake_images.data)
            gradient_penalty.backward()
            disc_loss = disc_out_fake - disc_out_real + gradient_penalty
        else:
            disc_loss = disc_out_real - disc_out_fake
        disc_opt.step()

    return gen_net, disc_net, disc_opt


def train_gen_net(gen_net, disc_net, gen_opt):
    gen_net.zero_grad()

    noise = torch.randn(BATCH_SIZE, NOISE_SIZE)
    if torch.cuda.is_available():
        noise = noise.to(DEVICE)
    noise_var = autograd.Variable(noise)
    gen_out = gen_net(noise_var)
    net_out = disc_net(gen_out)
    net_out = net_out.mean()
    net_out.backward(torch.FloatTensor([-1].to(DEVICE)))
    gen_loss = -net_out
    gen_opt.step()
    return gen_net, disc_net, gen_opt


def train_loop(gen_net, disc_net, gen_opt, disc_opt, train_gen, dev_gen, use_gradient_penalty):
    data_gen = images_train_gen(train_gen)

    for iteration in range(GEN_ITER):
        start_time = time.time()

        for p in disc_net.parameters():  # reset requires_grad
            p.requires_grad = True

        gen_net, disc_net, disc_opt = train_disc_net(gen_net, disc_net, data_gen, disc_opt,
                                                     use_gradient_penalty=use_gradient_penalty)

        for p in disc_net.parameters():  # reset requires_grad
            p.requires_grad = False

        gen_net, disc_net, gen_opt = train_gen_net(gen_net, disc_net, gen_opt)

        if iteration % PRINT_EVERY == (PRINT_EVERY - 1):
            dev_disc_losses = []
            for dev_images, _ in dev_gen():
                dev_images = torch.Tensor(dev_images)

                if is_gpu_available:
                    dev_images = dev_images.to(DEVICE)
                dev_images_var = autograd.Variable(dev_images, volatile=True)

                disc_out = disc_net(dev_images_var)
                dev_disc_loss = -disc_out.mean().cpu().data.numpy()
                dev_disc_losses.append(dev_disc_loss)
            image = generate_fake_image(gen_net)

