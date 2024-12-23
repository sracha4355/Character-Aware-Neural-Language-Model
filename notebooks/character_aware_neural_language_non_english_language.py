# -*- coding: utf-8 -*-
"""character_aware_neural_language_non_english_language.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1COioOgPv8mM21-4zoqJaHqiGRvapNPXx
"""

import torch
import torch.nn.functional as F
from torch import nn
from torch.utils.data import Dataset
from spacy.tokenizer import Tokenizer
from spacy.lang.en import English
from torch.utils.data import DataLoader
from conllu import parse
import collections
import time
import os
import re
import math

FILEPATH_TO_DATA_FOLDER = ""
FILEPATH_TO_FOLDER_TO_SAVE_MODEL = ""

def load_checkpoint(checkpoint_filepath, model, optimizer):
  checkpoint = torch.load(checkpoint_filepath)
  model.load_state_dict(checkpoint['model_state_dict'])
  optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
  epoch = checkpoint['epoch']
  loss = checkpoint['loss']
  print(f'Checkpoint loaded from {checkpoint_filepath} starting at epoch: {epoch}, current_loss: {loss}')
  return epoch, loss

def save_checkpoint(epoch, loss, model, optimizer, checkpoint_filepath):
  torch.save({
    'epoch': epoch,
    'model_state_dict': model.state_dict(),
    'optimizer_state_dict': optimizer.state_dict(),
    'loss': loss
  }, checkpoint_filepath)

def delete_checkpoint_files():
  regex = re.compile(r"^checkpoint.*")
  for filename in os.listdir('./'):
    if regex.match(filename):
      try:
        os.remove(filename)
        print(f'deleted {filename}')
      except OSError as e:
        print(e)

class UDTeluguDataloader(Dataset):
  def __init__(
      self,
      split="train",
      directory_path="",
      batch_size=20
    ):

    self.characters = []
    self.tokens = []
    self.splits = {}
    self.split = split
    self.batch_size = batch_size

    # create mappings for each character and token type
    self.char_to_int = {}
    self.token_to_int = {}
    self.int_to_char = {}
    self.int_to_token = {}

    for split in ["dev", "test", "train"]:
      file = f'{FILEPATH_TO_DATA_FOLDER}/te_mtg-ud-{split}.conllu'
      content = ""
      with open(file, "r") as file:
        content = file.read()

      sentences = parse(content)
      for token_list in sentences:
        for token in token_list:
          #print(token['form'])
          self.tokens.append(token['form'])
          for char in token['form']:
            self.characters.append(char)

    # populate the encoding mappings
    self.tokens = set(sorted(self.tokens))
    self.characters = set(sorted(self.characters))
    for i, token in enumerate(self.tokens):
      self.token_to_int[token] = i
      self.int_to_token[i] = token

    for i, character in enumerate(self.characters):
      self.char_to_int[character] = i
      self.int_to_char[i] = character

    assert len(self.char_to_int) == len(self.int_to_char)
    assert len(self.token_to_int) == len(self.int_to_token)
    assert len(self.char_to_int) == len(self.characters)
    assert len(self.token_to_int) == len(self.tokens)

    for split in ["dev", "test", "train"]:
      file = f'{FILEPATH_TO_DATA_FOLDER}/te_mtg-ud-{split}.conllu'
      content = ""
      with open(file, "r") as file:
        content = file.read()

      # encode the tokens
      encoded_tokens = []
      sentences = parse(content)
      for token_list in sentences:
        for token in token_list:
          encoded_tokens.append([])
          for char in token['form']:
            encoded_tokens[-1].append(
                self.char_to_int[char]
            )
      self.splits[split] = encoded_tokens

  def __len__(self):
    return ((len(self.splits[self.split]) - 1) // self.batch_size)

  def __getitem__(self, index, as_tensor=False):
    if index == self.__len__():
      raise IndexError(f'Provide an index between 0 and {self.__len__() + 1}')

    cutoff = self.__len__() * self.batch_size
    token = self.splits\
     [self.split]\
     [index * self.batch_size : min(index * self.batch_size + self.batch_size, cutoff)]

    prediction = self.splits[self.split][min(index * self.batch_size + self.batch_size, cutoff)]

    characters = []
    for character in prediction:
      characters.append(self.int_to_char[character])
    prediction = self.token_to_int["".join(characters)]

    if as_tensor:
      return torch.tensor(token), torch.tensor(prediction)
    return token, prediction

  def change_split(self, split):
    if split != "dev" and split != "train" and split != "test":
        raise ValueError("Provide a valid split. Options are dev, test, train.")
    self.split = split
    return self

  def get_encoding_mappings(self):
    return self.char_to_int, self.int_to_char, self.token_to_int, self.int_to_token

EMBEDDING_DIM = 16

SMALL = [(w, 25 * w) for w in range(1, 7)]
LARGE = [(w, min(200, w * 50)) for w in range(1, 8)]

# responsibility of this class is getting encoded tokens and converting them to char embeddings
class CharEmbeddings(nn.Module):
  def __init__(self, num_chars, embedding_dim, max_token_length, device):
    super(CharEmbeddings, self).__init__()
    self.embedding_dim = embedding_dim
    self.max_token_length = max_token_length
    self.start_of_word_idx = num_chars
    self.end_of_word_idx = num_chars + 1
    self.device = device
    self.embeddings = nn.Embedding(num_chars + 2, embedding_dim).to(self.device)


  # change this to return the transformed characters in batches
  def forward(self, tokens):
    batch_size = len(tokens[0])
    batches = len(tokens)
    _embeddings = []

    for batch in range(batches):
      embeddings_for_batch = []
      for index, token_list in enumerate(tokens[batch]):
        character_embeddings = self.embeddings(torch.tensor([self.start_of_word_idx] + token_list + [self.end_of_word_idx], dtype=torch.long, device=self.device))
        character_embeddings = torch.nn.functional.pad(
            character_embeddings,
            (0,0,0, self.max_token_length + 2 - character_embeddings.shape[0])
        )

        embeddings_for_batch.append(character_embeddings)

      _embeddings.append(
          torch.stack(embeddings_for_batch, dim=0)
      )

    tensor = torch.stack(
        _embeddings, dim=0
    )

    return tensor


class CharCNN(nn.Module):
  def __init__(self, embedding_dim, activation, filter_width_mapping, device):
    super().__init__()
    self.embedding_dim = embedding_dim
    self.activation = activation
    self.conv_layers = nn.ModuleList()
    self.total_num_filters = 0
    self.device = device

    # filter_width_mapping is a set of tuples
    # (int, int)
    for width, num_filters in filter_width_mapping:
      self.conv_layers.append(
          nn.Conv2d (
            in_channels=1,
            out_channels =num_filters,
            kernel_size=(width, self.embedding_dim),
            padding=0,
            stride=1,
            bias=True
        ).to(self.device)
      )
      self.total_num_filters += num_filters

  def forward(self, tokens):
    tokens = tokens.to(self.device)
    convolution_results = []

    for conv_layer in self.conv_layers:
      x = conv_layer(tokens)
      x = torch.squeeze(x, dim=3)
      x = self.activation(x)
      max_over_time, _ = torch.max(x, dim=2, keepdim=False)
      convolution_results.append(max_over_time)

    x = torch.cat(convolution_results,dim=1)
    return x

class HighwayNetwork(nn.Module):
  def __init__(self, layers, activation, input_size, device):
    super().__init__()
    self.activation = activation
    self.layers = layers
    self.device = device
    self.highway_matrices = nn.ModuleList([
        nn.Linear(input_size, input_size, bias=True).to(device)
        for _ in range(layers * 2)
    ])

  def _highway_layers(self, y):
    z = y
    for i in range(self.layers):
      highway_gate, transform_gate = self.highway_matrices[i * 2], self.highway_matrices[i * 2 + 1]
      t = F.sigmoid(transform_gate(z))
      z = t * self.activation(highway_gate(z)) + (1 - t) * z
    return z

  def forward(self, tokens):
    shape = tokens.shape
    tokens = torch.reshape(tokens, (tokens.shape[1], tokens.shape[2]))
    highway_output = []
    for row in tokens:
      row = torch.unsqueeze(row, dim=0)
      highway_output.append(self._highway_layers(row))

    highway_output = torch.stack(highway_output, dim=0).reshape(shape)
    return highway_output

class character_aware_nlm(nn.Module):
  def __init__(
      self,
      max_token_length,
      embedding_dim,
      num_characters,
      activation,
      filter_width_mapping,
      batch_size,
      num_highway_layers,
      highway_activation,
      num_rnn_layers,
      rnn_hidden_units,
      vocab_size,
      device,
      dropout=0.5,
      output_layer_dropout=0.5
    ):
    super().__init__()
    self.CharEmbeddingModule = CharEmbeddings(num_characters, embedding_dim, max_token_length, device)
    self.CharCNNModule = CharCNN(embedding_dim, activation, filter_width_mapping, device)
    self.HighwayNetworkModule = HighwayNetwork(num_highway_layers, highway_activation, self.CharCNNModule.total_num_filters, device)
    self.lstm = nn.LSTM(
        input_size=self.CharCNNModule.total_num_filters,
        num_layers=num_rnn_layers,
        hidden_size=rnn_hidden_units,
        batch_first=True,
        dropout=dropout
      ).to(device)
    self.word_prediction_layer = nn.Linear(rnn_hidden_units, vocab_size).to(device)
    self.prediction_layer_dropout = nn.Dropout(output_layer_dropout)
    self.device = device

  def forward(self, tokens):
    batches = len(tokens)
    batch_size = len(tokens[0])
    character_embeddings = self.CharEmbeddingModule(tokens)

    output = []
    for batch in range(character_embeddings.shape[0]):
      tensor = torch.unsqueeze(character_embeddings[batch], dim=1).to(self.device)
      output.append(self.CharCNNModule(tensor))
    output = torch.stack(output, dim=0)
    highway_output = self.HighwayNetworkModule(output)
    return self.lstm_forward(highway_output)

  def lstm_forward(self, input):
    out, (hidden, cell) = self.lstm(input)
    out = out[:, out.shape[1] - 1:out.shape[1], :].reshape(out.shape[0], -1)
    out = self.prediction_layer_dropout(out)
    return self.word_prediction_layer(out)

  def predict(self, training_data, get_logits=False):
    logits = self.forward(training_data)
    if get_logits:
      return logits

    logits = F.softmax(logits, dim=1)
    logits = torch.flatten(logits)
    max_logit, prediction_index = torch.max(logits, dim=0)
    return prediction_index

training_data = UDTeluguDataloader(split="train")
dev_data = UDTeluguDataloader(split="dev")
test_data = UDTeluguDataloader(split="test")

SMALL = [(w, 25 * w) for w in range(1, 7)]
LARGE = [(w, min(200, w * 50)) for w in range(1, 8)]

USE_CHECKPOINT = False
CHECKPOINT_FILEPATH = "checkpoint.pth"



def evaluate_model(model, dataloader):
    model.eval()
    total_loss = 0.0
    total_tokens = 0
    loss_fn = torch.nn.CrossEntropyLoss(reduction='sum')
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    with torch.no_grad():
         for index, tokens_and_prediction in enumerate(dataloader):
            tokens, prediction = tokens_and_prediction
            prediction = torch.tensor([prediction], dtype=torch.long, device=device)
            logits = model([tokens])
            loss = loss_fn(logits, prediction)

            total_loss += loss.item()
            total_tokens += prediction.numel()

    avg_nll = total_loss / total_tokens
    perplexity = math.exp(avg_nll)
    return perplexity

max_word_length = -1
for word in training_data.tokens:
    max_word_length = max(len(word), max_word_length)

model_args = {
      "max_token_length": max_word_length,
      "embedding_dim": 15,
      "num_characters": len(training_data.characters),
      "activation": F.tanh,
      "filter_width_mapping": SMALL,
      "batch_size": 20,
      "num_highway_layers": 1,
      "highway_activation": F.relu,
      "num_rnn_layers": 2,
      "rnn_hidden_units": 300,
      "vocab_size": len(training_data.tokens),
      "device": torch.device("cuda" if torch.cuda.is_available() else "cpu"),
      "dropout": 0,
      "output_layer_dropout":0.5
}

def train_model(
    optim,
    model_args,
    train_dataloader,
    dev_dataloader,
    _batch_size=20,
    _epochs=25,
    checkpoint=None
):

  EPOCHS = _epochs
  BATCH_SIZE = _batch_size
  LEARNING_RATE = 1

  train_dataloader.batch_size = BATCH_SIZE
  dev_dataloader.batch_size = BATCH_SIZE

  max_word_length = -1
  for word in train_dataloader.tokens:
    max_word_length = max(len(word), max_word_length)

  loss_fn = torch.nn.CrossEntropyLoss()
  device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
  model = None
  start_epoch = 0
  loss = 0

  if checkpoint == None:
    model = character_aware_nlm(
        **model_args
    )
    optim = torch.optim.SGD(model.parameters(), lr=LEARNING_RATE)
  else:
    # load from the check point
    start_epoch, loss = load_checkpoint(CHECKPOINT_FILEPATH, model, optim)

  for name, param in model.named_parameters():
    if 'weight' in name:
      nn.init.uniform_(param, -0.05, 0.05)
    elif 'bias' in name:
      nn.init.constant_(param, 0)
  ppl = None
  dataloader = DataLoader(train_dataloader, shuffle=True)
  for epoch in range(start_epoch, EPOCHS):
    model.train()
    total_loss = 0
    start_time = time.time()

    for index, tokens_and_prediction in enumerate(train_dataloader):
      tokens, prediction = tokens_and_prediction

      prediction = torch.tensor([prediction], dtype=torch.long, device=device)
      optim.zero_grad()
      logits = model([tokens])

      loss = loss_fn(logits, prediction)
      total_loss += loss.item()

      # Clear gradients

      loss.backward()

      # Clip gradients
      torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=5)
      optim.step()

    new_ppl = evaluate_model(model, dev_dataloader)
    print(f'Epoch: {epoch + 1}, Loss: {total_loss} dev PPL this epoch: {new_ppl} Time taken to finish epoch: {time.time() - start_time}')

    # calculate perplexity
    if ppl == None:
      ppl = new_ppl
    else:
      if abs(new_ppl - ppl) <= 1.0:  # If perplexity improvement <= 1.0
        for param_group in optim.param_groups:
              param_group['lr'] /= 2  # Halve the learning rate
              param_group['lr'] = max(param_group['lr'], 0.1) # do not deep below 0.01
        print(f"Learning rate halved. New LR: {optim.param_groups[0]['lr']}")
      ppl = new_ppl

  return model.state_dict()

small_model_before_training = character_aware_nlm(
    **model_args
)
ppl_before_training = evaluate_model(small_model_before_training, test_data)
ppl_before_training_dev = evaluate_model(small_model_before_training, dev_data)

print(f'ppl on test set before training: {ppl_before_training}')
print(f'ppl on dev set before training: {ppl_before_training_dev}')

params_state_dict = train_model(optim=None, model_args=model_args, train_dataloader=training_data, dev_dataloader=dev_data, checkpoint=None, _epochs=5)

trained_model = character_aware_nlm(
    **model_args
)
trained_model.load_state_dict(params_state_dict)
torch.save(params_state_dict, f"{FILEPATH_TO_FOLDER_TO_SAVE_MODEL}/small-variant-telugu.pth")

ppl_on_test_data = evaluate_model(trained_model, test_data)

print('ppl on test data', ppl_on_test_data)
