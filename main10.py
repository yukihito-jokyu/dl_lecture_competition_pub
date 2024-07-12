"""
main7.pyの修正
学習済みモデルの適用
BERTのトークナイザー
"""

import re
import random
import time
from statistics import mode

from PIL import Image
import numpy as np
import pandas
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision
from torchvision import transforms

from tqdm import tqdm

from torchvision.models import vit_b_16, ViT_B_16_Weights

def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def process_text(text):
    # lowercase
    text = text.lower()

    # 数詞を数字に変換
    num_word_to_digit = {
        'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4',
        'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9',
        'ten': '10'
    }
    for word, digit in num_word_to_digit.items():
        text = text.replace(word, digit)

    # 小数点のピリオドを削除
    text = re.sub(r'(?<!\d)\.(?!\d)', '', text)

    # 冠詞の削除
    text = re.sub(r'\b(a|an|the)\b', '', text)

    # 短縮形のカンマの追加
    contractions = {
        "dont": "don't", "isnt": "isn't", "arent": "aren't", "wont": "won't",
        "cant": "can't", "wouldnt": "wouldn't", "couldnt": "couldn't"
    }
    for contraction, correct in contractions.items():
        text = text.replace(contraction, correct)

    # 句読点をスペースに変換
    text = re.sub(r"[^\w\s':]", ' ', text)

    # 句読点をスペースに変換
    text = re.sub(r'\s+,', ',', text)

    # 連続するスペースを1つに変換
    text = re.sub(r'\s+', ' ', text).strip()

    return text


# 1. データローダーの作成
class VQADataset(torch.utils.data.Dataset):
    def __init__(self, df_path, image_dir, transform=None, answer=True):
        self.transform = transform  # 画像の前処理
        self.image_dir = image_dir  # 画像ファイルのディレクトリ
        self.df = pandas.read_json(df_path)  # 画像ファイルのパス，question, answerを持つDataFrame
        self.answer = answer

        # question / answerの辞書を作成
        self.vocab2idx = {}
        self.idx2vocab = {}
        self.question2idx = {}
        self.answer2idx = {}
        self.idx2question = {}
        self.idx2answer = {}
        self.max_sequence = 56

        # paddingとunknownを定義
        self.question2idx['<PAD>'] = 0
        self.question2idx['<UNK>'] = 1
        self.idx2question[0] = '<PAD>'
        self.idx2question[1] = '<UNK>'

        # 質問文に含まれる単語を辞書に追加
        for question in self.df["question"]:
            question = process_text(question)
            words = question.split(" ")
            self.max_sequence = max(self.max_sequence, len(words))
            for word in words:
                if word not in self.question2idx:
                    self.question2idx[word] = len(self.question2idx)
                # 追加
                if word not in self.vocab2idx:
                    self.vocab2idx[word] = len(self.vocab2idx)

        self.idx2question = {v: k for k, v in self.question2idx.items()}  # 逆変換用の辞書(question)

        if self.answer:
            # 回答に含まれる単語を辞書に追加
            for answers in self.df["answers"]:
                for answer in answers:
                    word = answer["answer"]
                    word = process_text(word)
                    if word not in self.answer2idx:
                        self.answer2idx[word] = len(self.answer2idx)
                    # 追加
                    if word not in self.vocab2idx:
                        self.vocab2idx[word] = len(self.vocab2idx)
            
            # 追加
            self.idx2vocab = {v: k for k, v in self.vocab2idx.items()}

            self.idx2answer = {v: k for k, v in self.answer2idx.items()}  # 逆変換用の辞書(answer)

    def update_dict(self, dataset):
        """
        検証用データ，テストデータの辞書を訓練データの辞書に更新する．

        Parameters
        ----------
        dataset : Dataset
            訓練データのDataset
        """
        self.question2idx = dataset.question2idx
        self.answer2idx = dataset.answer2idx
        self.vocab2idx = dataset.vocab2idx
        self.idx2question = dataset.idx2question
        self.idx2answer = dataset.idx2answer
        self.idx2vocab = dataset.idx2vocab

    def __getitem__(self, idx):
        """
        対応するidxのデータ（画像，質問，回答）を取得．

        Parameters
        ----------
        idx : int
            取得するデータのインデックス

        Returns
        -------
        image : torch.Tensor  (C, H, W)
            画像データ
        question : torch.Tensor  (vocab_size)
            質問文をone-hot表現に変換したもの
        answers : torch.Tensor  (n_answer)
            10人の回答者の回答のid
        mode_answer_idx : torch.Tensor  (1)
            10人の回答者の回答の中で最頻値の回答のid
        """
        # print(idx)
        # print(self.df["question"][idx])
        sentence = ''
        image = Image.open(f"{self.image_dir}/{self.df['image'][idx]}")
        image = self.transform(image)
        # question = np.zeros(len(self.idx2question) + 1)  # 未知語用の要素を追加
        question = np.zeros(self.max_sequence) # 変更
        question_words = process_text(self.df["question"][idx]).split(" ")
        question_length = len(question_words)
        # 追加
        sentence = ' '.join(question_words)
        for index, word in enumerate(question_words):
            try:
                # question[self.question2idx[word]] = 1  # one-hot表現に変換
                # question[index] = self.vocab2idx[process_text(word)] # 変更
                question[index] = self.question2idx[word]
            except KeyError:
                # print(word)
                question[index] = 1  # 未知語

        if self.answer:
            # answers = [self.answer2idx[process_text(answer["answer"])] for answer in self.df["answers"][idx]]
            answers = [self.answer2idx[process_text(answer["answer"])] for answer in self.df["answers"][idx]]
            mode_answer_idx = mode(answers)  # 最頻値を取得（正解ラベル）

            return image, torch.Tensor(question), torch.Tensor([question_length]), torch.Tensor(answers), int(mode_answer_idx)

        else:
            return image, torch.Tensor(question), torch.Tensor([question_length])

    def __len__(self):
        return len(self.df)


# 2. 評価指標の実装
# 簡単にするならBCEを利用する
def VQA_criterion(batch_pred: torch.Tensor, batch_answers: torch.Tensor):
    total_acc = 0.

    for pred, answers in zip(batch_pred, batch_answers):
        acc = 0.
        for i in range(len(answers)):
            num_match = 0
            for j in range(len(answers)):
                if i == j:
                    continue
                if pred == answers[j]:
                    num_match += 1
            acc += min(num_match / 3, 1)
        total_acc += acc / 10

    return total_acc / len(batch_pred)


# 3. モデルのの実装
# ResNetを利用できるようにしておく
class BasicBlock(nn.Module):
    expansion = 1

    def __init__(self, in_channels: int, out_channels: int, stride: int = 1):
        super().__init__()

        self.conv1 = nn.Conv2d(in_channels, out_channels, kernel_size=3, stride=stride, padding=1)
        self.bn1 = nn.BatchNorm2d(out_channels)
        self.conv2 = nn.Conv2d(out_channels, out_channels, kernel_size=3, stride=1, padding=1)
        self.bn2 = nn.BatchNorm2d(out_channels)
        self.relu = nn.ReLU(inplace=True)

        self.shortcut = nn.Sequential()
        if stride != 1 or in_channels != out_channels:
            self.shortcut = nn.Sequential(
                nn.Conv2d(in_channels, out_channels, kernel_size=1, stride=stride),
                nn.BatchNorm2d(out_channels)
            )

    def forward(self, x):
        residual = x
        out = self.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))

        out += self.shortcut(residual)
        out = self.relu(out)

        return out


class BottleneckBlock(nn.Module):
    expansion = 4

    def __init__(self, in_channels: int, out_channels: int, stride: int = 1):
        super().__init__()

        self.conv1 = nn.Conv2d(in_channels, out_channels, kernel_size=1, stride=1)
        self.bn1 = nn.BatchNorm2d(out_channels)
        self.conv2 = nn.Conv2d(out_channels, out_channels, kernel_size=3, stride=stride, padding=1)
        self.bn2 = nn.BatchNorm2d(out_channels)
        self.conv3 = nn.Conv2d(out_channels, out_channels * self.expansion, kernel_size=1, stride=1)
        self.bn3 = nn.BatchNorm2d(out_channels * self.expansion)
        self.relu = nn.ReLU(inplace=True)

        self.shortcut = nn.Sequential()
        if stride != 1 or in_channels != out_channels * self.expansion:
            self.shortcut = nn.Sequential(
                nn.Conv2d(in_channels, out_channels * self.expansion, kernel_size=1, stride=stride),
                nn.BatchNorm2d(out_channels * self.expansion)
            )

    def forward(self, x):
        residual = x
        out = self.relu(self.bn1(self.conv1(x)))
        out = self.relu(self.bn2(self.conv2(out)))
        out = self.bn3(self.conv3(out))

        out += self.shortcut(residual)
        out = self.relu(out)

        return out


class ResNet(nn.Module):
    def __init__(self, block, layers):
        super().__init__()
        self.in_channels = 64

        self.conv1 = nn.Conv2d(3, 64, kernel_size=7, stride=2, padding=3)
        self.bn1 = nn.BatchNorm2d(64)
        self.relu = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)

        self.layer1 = self._make_layer(block, layers[0], 64)
        self.layer2 = self._make_layer(block, layers[1], 128, stride=2)
        self.layer3 = self._make_layer(block, layers[2], 256, stride=2)
        self.layer4 = self._make_layer(block, layers[3], 512, stride=2)

        self.avgpool = nn.AdaptiveAvgPool2d((1, 1))
        self.fc = nn.Linear(512 * block.expansion, 512)

    def _make_layer(self, block, blocks, out_channels, stride=1):
        layers = []
        layers.append(block(self.in_channels, out_channels, stride))
        self.in_channels = out_channels * block.expansion
        for _ in range(1, blocks):
            layers.append(block(self.in_channels, out_channels))

        return nn.Sequential(*layers)

    def forward(self, x):
        x = self.relu(self.bn1(self.conv1(x)))
        x = self.maxpool(x)

        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)

        x = self.avgpool(x)
        feature_vector = x.view(x.size(0), -1)
        x = self.fc(feature_vector)

        return x, feature_vector


def ResNet18():
    return ResNet(BasicBlock, [2, 2, 2, 2])


def ResNet50():
    return ResNet(BottleneckBlock, [3, 4, 6, 3])


class CompactBilinearPooling(nn.Module):
    def __init__(self, input_dim1, input_dim2, output_dim):
        super().__init__()
        self.output_dim = output_dim
        self.h1 = torch.randint(0, output_dim, (input_dim1,))
        self.h2 = torch.randint(0, output_dim, (input_dim2,))
        self.s1 = 2 * torch.randint(0, 2, (input_dim1,)) - 1
        self.s2 = 2 * torch.randint(0, 2, (input_dim2,)) - 1

    def forward(self, x, y):
        fft1 = torch.fft.fft(self.s1.to(x.device) * x, n=self.output_dim, dim=-1)
        fft2 = torch.fft.fft(self.s2.to(y.device) * y, n=self.output_dim, dim=-1)
        fft_product = fft1 * fft2
        cbp = torch.fft.ifft(fft_product, dim=-1).real
        return cbp / self.output_dim


class VQAModel(nn.Module):
    def __init__(self, vocab_size: int, embed_size: int, hidden_size: int, n_answer: int, dropout_rate: float):
        super().__init__()
        # self.resnet = ResNet18()
        # ResNetをViTに置き換え
        self.vit = vit_b_16(weights=ViT_B_16_Weights.DEFAULT)
        # 最後の分類層を削除
        self.vit.heads = nn.Identity()
        # 追加
        self.embedding = nn.Embedding(vocab_size, embed_size)
        self.lstm1 = nn.LSTM(embed_size, hidden_size, batch_first=True)
        self.dropout1 = nn.Dropout(dropout_rate)
        self.lstm2 = nn.LSTM(embed_size, hidden_size, batch_first=True)
        self.dropout2 = nn.Dropout(dropout_rate)

        # self.text_encoder = nn.Linear(vocab_size, 512)
        self.cbp = CompactBilinearPooling(768, hidden_size, 1024)

        self.fc = nn.Sequential(
            nn.Linear(1024, 512),
            nn.ReLU(inplace=True),
            nn.Linear(512, n_answer)
        )

    def forward(self, image, question):
        # image_feature = self.resnet(image)  # 画像の特徴量
        image_feature = self.vit(image)
        # question_feature = self.text_encoder(question)  # テキストの特徴量

        # 追加
        question = question.long()
        embedded = self.embedding(question)
        out, (question_feature, _) = self.lstm1(embedded)
        out = self.dropout1(out)
        out, (question_feature, _) = self.lstm2(out)
        question_feature = question_feature[-1]

        fused_feature = self.cbp(image_feature, question_feature)

        fused_feature = F.normalize(fused_feature, p=2, dim=-1)

        # x = torch.cat([image_feature, question_feature], dim=1)
        x = self.fc(fused_feature)
        # x = self.fc(x)
        return x


# 4. 学習の実装
def train(model, dataloader, optimizer, criterion, device):
    model.train()

    total_loss = 0
    total_acc = 0
    simple_acc = 0

    start = time.time()
    for image, question, question_length, answers, mode_answer in tqdm(dataloader):
        # print('question:', question)
        # print('answers:', answers, answers.size())
        # print('mode_answer.squeeze():', mode_answer.squeeze(), mode_answer.squeeze().size())
        # print('mode_answer:', mode_answer, mode_answer.size())
        image, question, question_length, answer, mode_answer = \
            image.to(device), question.to(device), question_length.to(device), answers.to(device), mode_answer.to(device)

        pred = model(image, question)
        loss = criterion(pred, mode_answer.squeeze())

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        total_loss += loss.item()
        total_acc += VQA_criterion(pred.argmax(1), answers)  # VQA accuracy
        simple_acc += (pred.argmax(1) == mode_answer).float().mean().item()  # simple accuracy
        # pass

    return total_loss / len(dataloader), total_acc / len(dataloader), simple_acc / len(dataloader), time.time() - start


def eval(model, dataloader, optimizer, criterion, device):
    model.eval()

    total_loss = 0
    total_acc = 0
    simple_acc = 0

    start = time.time()
    for image, question, question_length, answers, mode_answer in dataloader:
        image, question, question_length, answer, mode_answer = \
            image.to(device), question.to(device), question_length.to(device), answers.to(device), mode_answer.to(device)

        pred = model(image, question)
        loss = criterion(pred, mode_answer.squeeze())

        total_loss += loss.item()
        total_acc += VQA_criterion(pred.argmax(1), answers)  # VQA accuracy
        simple_acc += (pred.argmax(1) == mode_answer).mean().item()  # simple accuracy

    return total_loss / len(dataloader), total_acc / len(dataloader), simple_acc / len(dataloader), time.time() - start


def main():
    # deviceの設定
    set_seed(42)
    device = "cuda" if torch.cuda.is_available() else "cpu"

    # dataloader / model
    transform = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor()
    ])
    train_dataset = VQADataset(df_path="./data/train.json", image_dir="./data/train", transform=transform)
    print('train_load')
    print('vocab', len(train_dataset.question2idx)+1)
    test_dataset = VQADataset(df_path="./data/valid.json", image_dir="./data/valid", transform=transform, answer=False)
    print('test_load')
    test_dataset.update_dict(train_dataset)
    # print('train_max_sequence:', train_dataset.max_sequence)
    # print('test_max_sequence:', test_dataset.max_sequence)

    train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=256, shuffle=True)
    test_loader = torch.utils.data.DataLoader(test_dataset, batch_size=1, shuffle=False)

    # 追加
    embed_size = 512
    hidden_size = 512
    dropout_rate = 0.5

    model = VQAModel(vocab_size=len(train_dataset.vocab2idx)+1, embed_size=embed_size, hidden_size=hidden_size, n_answer=len(train_dataset.answer2idx), dropout_rate=dropout_rate).to(device)
    print('model_load')
    # optimizer / criterion
    num_epoch = 20
    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=1e-5)

    # train model
    for epoch in range(num_epoch):
        train_loss, train_acc, train_simple_acc, train_time = train(model, train_loader, optimizer, criterion, device)
        print(f"【{epoch + 1}/{num_epoch}】\n"
              f"train time: {train_time:.2f} [s]\n"
              f"train loss: {train_loss:.4f}\n"
              f"train acc: {train_acc:.4f}\n"
              f"train simple acc: {train_simple_acc:.4f}")

    # 提出用ファイルの作成
    model.eval()
    submission = []
    for image, question, _ in test_loader:
        image, question = image.to(device), question.to(device)
        pred = model(image, question)
        pred = pred.argmax(1).cpu().item()
        submission.append(pred)

    submission = [train_dataset.idx2answer[id] for id in submission]
    submission = np.array(submission)
    torch.save(model.state_dict(), "model.pth")
    np.save("submission10.npy", submission)

if __name__ == "__main__":
    main()