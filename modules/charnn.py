import re
import torch
import torch.nn as nn
import torch.utils.data
from torch import Tensor
from typing import Iterator


def char_maps(text: str):
    """
    Create mapping from the unique chars in a text to integers and
    vice-versa.
    :param text: Some text.
    :return: Two maps.
        - char_to_idx, a mapping from a character to a unique
        integer from zero to the number of unique chars in the text.
        - idx_to_char, a mapping from an index to the character
        represented by it. The reverse of the above map.

    """
    chars = []
    [chars.append(x) for x in text if x not in chars]
    chars.sort()
    idx = list(range(0, len(chars)))
    char_to_idx = dict(zip(chars, idx))
    idx_to_char = dict(zip(idx, chars))

    return char_to_idx, idx_to_char


def remove_chars(text: str, chars_to_remove):
    """
    Removes all occurrences of the given chars from a text sequence.
    :param text: The text sequence.
    :param chars_to_remove: A list of characters that should be removed.
    :return:
        - text_clean: the text after removing the chars.
        - n_removed: Number of chars removed.
    """

    text_clean = "".join(x for x in text if x not in chars_to_remove)
    n_removed = len(text) - len(text_clean)

    return text_clean, n_removed


def chars_to_onehot(text: str, char_to_idx: dict) -> Tensor:
    """
    Embed a sequence of chars as a a tensor containing the one-hot encoding
    of each char. A one-hot encoding means that each char is represented as
    a tensor of zeros with a single '1' element at the index in the tensor
    corresponding to the index of that char.
    :param text: The text to embed.
    :param char_to_idx: Mapping from each char in the sequence to it's
    unique index.
    :return: Tensor of shape (N, D) where N is the length of the sequence
    and D is the number of unique chars in the sequence. The dtype of the
    returned tensor will be torch.int8.
    """

    D = len(char_to_idx.values())
    N = len(text)
    result = torch.zeros(N,D, dtype = torch.int8)
    for i, char in enumerate(text):
        result[i, char_to_idx[char]] = 1

    return result


def onehot_to_chars(embedded_text: Tensor, idx_to_char: dict) -> str:
    """
    Reverses the embedding of a text sequence, producing back the original
    sequence as a string.
    :param embedded_text: Text sequence represented as a tensor of shape
    (N, D) where each row is the one-hot encoding of a character.
    :param idx_to_char: Mapping from indices to characters.
    :return: A string containing the text sequence represented by the
    embedding.
    """

    N, D = embedded_text.shape
    result = ""
    for token in embedded_text:
        result += idx_to_char[int((token.nonzero()).item())]

    return result


def chars_to_labelled_samples(text: str, char_to_idx: dict, seq_len: int, device="cpu"):
    """
    Splits a char sequence into smaller sequences of labelled samples.
    A sample here is a sequence of seq_len embedded chars.
    Each sample has a corresponding label, which is also a sequence of
    seq_len chars represented as indices. The label is constructed such that
    the label of each char is the next char in the original sequence.
    :param text: The char sequence to split.
    :param char_to_idx: The mapping to create and embedding with.
    :param seq_len: The sequence length of each sample and label.
    :param device: The device on which to create the result tensors.
    :return: A tuple containing two tensors:
    samples, of shape (N, S, V) and labels of shape (N, S) where N is
    the number of created samples, S is the seq_len and V is the embedding
    dimension.
    """

    text_as_onehots = chars_to_onehot(text, char_to_idx)
    N = (len(text)-1)//seq_len
    V = len(char_to_idx)
    remainder = -1*(len(text_as_onehots) % seq_len)

    samples = text_as_onehots[:remainder].reshape((N, seq_len, V))

    labels = text_as_onehots[1:1+remainder].reshape((N, seq_len, V))
    labels = torch.argmax(labels, dim=2)

    return samples, labels


def hot_softmax(y, dim=0, temperature=1.0):
    """
    A softmax which first scales the input by 1/temperature and
    then computes softmax along the given dimension.
    :param y: Input tensor.
    :param dim: Dimension to apply softmax on.
    :param temperature: Temperature.
    :return: Softmax computed with the temperature parameter.
    """

    y = y/temperature
    result = torch.softmax(y, dim)

    return result


def generate_from_model(model, start_sequence, n_chars, char_maps, T):
    """
    Generates a sequence of chars based on a given model and a start sequence.
    :param model: An RNN model. forward should accept (x,h0) and return (y,
    h_s) where x is an embedded input sequence, h0 is an initial hidden state,
    y is an embedded output sequence and h_s is the final hidden state.
    :param start_sequence: The initial sequence to feed the model.
    :param n_chars: The total number of chars to generate (including the
    initial sequence).
    :param char_maps: A tuple as returned by char_maps(text).
    :param T: Temperature for sampling with softmax-based distribution.
    :return: A string starting with the start_sequence and continuing for
    with chars predicted by the model, with a total length of n_chars.
    """
    assert len(start_sequence) < n_chars
    device = next(model.parameters()).device
    char_to_idx, idx_to_char = char_maps
    out_text = start_sequence

    x = chars_to_onehot(start_sequence, char_to_idx)
    x = x.unsqueeze(0).float()
    h = None

    while len(out_text) < n_chars:
        x = x.to(device=device)
        
        y_pred, h = model(x, h)
        y = hot_softmax(y_pred[0,-1,:], temperature=T) 
        y = torch.multinomial(y, 1) 
        
        new_char = idx_to_char[y.item()]
        x = chars_to_onehot(new_char, char_to_idx).unsqueeze(0).float().to(device)
        out_text += new_char  

    return out_text


class SequenceBatchSampler(torch.utils.data.Sampler):
    """
    Samples indices from a dataset containing consecutive sequences.
    This sample ensures that samples in the same index of adjacent
    batches are also adjacent in the dataset.
    """

    def __init__(self, dataset: torch.utils.data.Dataset, batch_size):
        """
        :param dataset: The dataset for which to create indices.
        :param batch_size: Number of indices in each batch.
        """
        super().__init__(dataset)
        self.dataset = dataset
        self.batch_size = batch_size

    def __iter__(self) -> Iterator[int]:

        N = len(self.dataset) - len(self.dataset) % self.batch_size
        idx = torch.reshape(torch.arange(0, N), (self.batch_size, -1)).T.flatten()

        return iter(idx)

    def __len__(self):
        return len(self.dataset)


class MultilayerGRU(nn.Module):
    """
    Represents a multi-layer GRU (gated recurrent unit) model.
    """

    def __init__(self, in_dim, h_dim, out_dim, n_layers, dropout=0):
        """
        :param in_dim: Number of input dimensions (at each timestep).
        :param h_dim: Number of hidden state dimensions.
        :param out_dim: Number of input dimensions (at each timestep).
        :param n_layers: Number of layer in the model.
        :param dropout: Level of dropout to apply between layers. Zero
        disables.
        """
        super().__init__()
        assert in_dim > 0 and h_dim > 0 and out_dim > 0 and n_layers > 0

        self.in_dim = in_dim
        self.out_dim = out_dim
        self.h_dim = h_dim
        self.n_layers = n_layers
        self.layer_params = []

        self.dropout = nn.Dropout(p=dropout)

        in_features = self.in_dim

        for i in range(self.n_layers):
            
            xz = nn.Linear(in_features=in_features, out_features=self.h_dim, bias = False)
            hz = nn.Linear(in_features=self.h_dim, out_features=self.h_dim)

            xr = nn.Linear(in_features=in_features, out_features=self.h_dim, bias = False)
            hr = nn.Linear(in_features=self.h_dim, out_features=self.h_dim)

            xg = nn.Linear(in_features=in_features, out_features=self.h_dim, bias = False)
            hg = nn.Linear(in_features=self.h_dim, out_features=self.h_dim)

            in_features = self.h_dim

            for module, name in zip([hz, xz, hr, xr, hg, xg], ['hz', 'xz', 'hr', 'xr', 'hg', 'xg']):
                self.add_module(f'Layer_{i}_{name}', module)
    
            self.layer_params.append([hz, xz, hr, xr, hg, xg])

        hy = nn.Linear(in_features=self.h_dim, out_features=self.out_dim) 
        self.layer_params.append([hy])
        self.add_module(f'Last_layer', hy)

    def forward(self, input: Tensor, hidden_state: Tensor = None):
        """
        :param input: Batch of sequences. Shape should be (B, S, I) where B is
        the batch size, S is the length of each sequence and I is the
        input dimension (number of chars in the case of a char RNN).
        :param hidden_state: Initial hidden state per layer (for the first
        char). Shape should be (B, L, H) where B is the batch size, L is the
        number of layers, and H is the number of hidden dimensions.
        :return: A tuple of (layer_output, hidden_state).
        The layer_output tensor is the output of the last RNN layer,
        of shape (B, S, O) where B,S are as above and O is the output
        dimension.
        The hidden_state tensor is the final hidden state, per layer, of shape
        (B, L, H) as above.
        """
        batch_size, seq_len, _ = input.shape

        layer_states = []
        for i in range(self.n_layers):
            if hidden_state is None:
                layer_states.append(
                    torch.zeros(batch_size, self.h_dim, device=input.device)
                )
            else:
                layer_states.append(hidden_state[:, i, :])

        layer_output = []

        for t in range(seq_len): # run over time           
            x = input[:, t, :]
            for i in range(self.n_layers): # run over layers
                gru_layers = self.layer_params[i]           
                z = nn.Sigmoid()(gru_layers[0](layer_states[i]) + gru_layers[1](x))
                r = nn.Sigmoid()(gru_layers[2](layer_states[i]) + gru_layers[3](x))
                g = nn.Tanh()(gru_layers[4](r*layer_states[i]) + gru_layers[5](x))
                h = z*layer_states[i] + (1-z)*g
                layer_states[i] = h
                x = self.dropout(h)

            layer_output.append(self.layer_params[-1][0](h))

        layer_output = torch.stack(layer_output, dim=1)
        hidden_state = torch.stack(layer_states, dim=1)        
        
                
        # ========================
        return layer_output, hidden_state
