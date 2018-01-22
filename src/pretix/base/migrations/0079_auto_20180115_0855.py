# -*- coding: utf-8 -*-
# Generated by Django 1.11.8 on 2018-01-15 08:55
from __future__ import unicode_literals

from django.db import migrations, models
from django.db.models import F
from django.db.models.functions import Concat


def set_full_invoice_no(app, schema_editor):
    Invoice = app.get_model('pretixbase', 'Invoice')
    Invoice.objects.all().update(
        full_invoice_no=Concat(F('prefix'), F('invoice_no'))
    )


class Migration(migrations.Migration):

    dependencies = [
        ('pretixbase', '0078_auto_20171206_1603'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoice',
            name='full_invoice_no',
            field=models.CharField(db_index=True, default='', max_length=190),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='question',
            name='type',
            field=models.CharField(choices=[('N', 'Number'), ('S', 'Text (one line)'), ('T', 'Multiline text'), ('B', 'Yes/No'), ('C', 'Choose one from a list'), ('M', 'Choose multiple from a list'), ('F', 'File upload'), ('D', 'Date'), ('H', 'Time'), ('W', 'Date and time')], max_length=5, verbose_name='Question type'),
        ),
        migrations.RunPython(
            set_full_invoice_no,
            migrations.RunPython.noop
        )
    ]