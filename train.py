import os
import time
import gpumemory
from keras import models
import caption

import os
import sys
import time
import importlib
import numpy as np
import words
from pprint import pprint


def train(**params):
    if params['training_mode'] == 'maximum-likelihood':
        train_ml(**params)
    else:
        train_pg(**params)


def train_ml(model_filename, epochs, batches_per_epoch, batch_size, **params):
    if model_filename == 'default_model':
        model_filename = 'model.caption.{}.h5'.format(int(time.time()))
    model = caption.build_model(**params)
    if os.path.exists(model_filename):
        model.load_weights(model_filename)
        # TODO: Use load_model to allow loaded architecture to differ from code
        # Fix problems with custom layers like CGRU
        #model = models.load_model(model_filename)

    model.summary()
    model.compile(optimizer='adam', loss='categorical_crossentropy', metrics=['accuracy'], decay=.01)
    tg = caption.training_generator(**params)
    for i in range(epochs):
        validate(model, **params)
        model.fit_generator(tg, batches_per_epoch)
        model.save(model_filename)
    print("Finished training {} epochs".format(epochs))


def validate(model, validation_count=5000, **params):
    g = caption.validation_generator(**params)
    print("Validating on {} examples...".format(validation_count))
    candidate_list = []
    references_list = []
    for _ in range(validation_count):
        validation_example = next(g)
        c, r = caption.evaluate(model, *validation_example, **params)
        print("{} ({})".format(c, r))
        candidate_list.append(c)
        references_list.append(r)
        scores = caption.get_scores(candidate_list, references_list)
        print scores


def train_pg(**params):
    model_filename = params['model_filename']
    batch_size = params['batch_size']

    model = caption.build_model(**params)
    model.load_weights(model_filename)
    model.summary()
    model.compile(optimizer='adam', loss='sparse_categorical_crossentropy', metrics=['accuracy'], decay=.01)

    horizon = 1
    rollouts = 10
    sampling_temperature = 0.3

    tg = caption.pg_training_generator(**params)

    for x, y, reference_texts in tg:
        # Roll out N random trajectories
        # For each one, get a BLEU-2 score
        # Choose the one with the highest BLEU-2
        # Turn it into a sequence of training examples!
        x_glob, x_loc, x_words, x_ctx = x
        prev_words = x_words

        # Wander outside of the training set
        for _ in range(horizon):
            x_words = np.roll(x_words, -1, axis=1)
            action_distribution = model.predict([x_glob, x_loc, x_words, x_ctx])
            x_words[:, -1] = [caption.sample(s) for s in action_distribution]


        # Now what's the best word? Not the ground truth.
        # Let's decide on the best word by randomly trying a few
        best_next_word = np.zeros(batch_size, dtype=int)
        best_score = np.zeros(batch_size)
        for r in range(rollouts):
            action_distribution = model.predict([x_glob, x_loc, x_words, x_ctx])
            new_words = np.roll(x_words, -1, axis=1)
            new_words[:, -1] = [caption.sample(s, temperature=sampling_temperature) for s in action_distribution]

            # Evaluate each sentence with it's extra word
            candidates = [words.words(s).strip('0 ') for s in new_words]
            bleu2_scores = [caption.bleu(c, r)[1] for (c, r) in zip(candidates, reference_texts)]

            for i in range(batch_size):
                if bleu2_scores[i] > best_score[i]:
                    best_score[i] = bleu2_scores[i]
                    best_next_word[i] = new_words[i, -1]
        print("Finished {} rollouts".format(r))

        ml_words = words.words(np.argmax(y, axis=-1)).split()
        pg_words = words.words(best_next_word).split()
        for i in range(batch_size):
            print("{} ... {} ({:.2f}) {}".format(words.words(x_words[i]), pg_words[i], best_score[i], reference_texts[i]))

        # Now we train using x_words and pg_words as the targets
        losses = model.train_on_batch([x_glob, x_loc, x_words, x_ctx], best_next_word)
        print(losses)

    print("OK done")

